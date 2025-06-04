# MCPO 连接管理功能（已优化）

本文档描述了 MCPO 中优化后的连接管理功能，用于解决与 Streamable HTTP/SSE MCP 服务器连接相关的问题。

## 🚀 最新优化（推荐）

基于专业分析，我们已经优化了连接管理策略：

- ❌ **移除了定期健康检查**（原1分钟检查一次）
- ✅ **改为智能被动检测**（仅在API调用时检测）
- ✅ **增加了连接状态缓存和错误计数**
- ✅ **保留按需健康检查端点**

## 问题背景

在使用 MCPO 连接到 MCP 服务器时，可能会遇到以下问题：

1. **连接失败**：网络问题或服务器暂时不可用导致连接失败
2. **连接超时**：服务器响应缓慢导致连接超时
3. **连接中断**：运行过程中连接意外断开
4. **500 错误**：连接问题导致的内部服务器错误

## 新增功能

### 1. 连接重试机制

当连接失败时，MCPO 会自动重试连接：

- **默认重试次数**：3次
- **重试延迟**：2秒（使用指数退避策略）
- **支持的连接类型**：Stdio、SSE、Streamable HTTP

```python
# 配置重试参数
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 2.0
```

### 2. 连接超时控制

为防止无限等待，添加了连接超时机制：

- **默认连接超时**：30秒
- **SSE 读取超时**：60秒（可配置）

```python
# 配置超时参数
DEFAULT_CONNECTION_TIMEOUT = 30.0
DEFAULT_SSE_READ_TIMEOUT = 60.0
```

### 3. 智能被动检测（优化后）

基于实际API调用的智能连接检测：

- **检测时机**：仅在用户调用工具时检测
- **检测方式**：基于API调用结果判断连接状态
- **状态缓存**：记录连接状态、错误次数和最后错误
- **自动恢复**：连接成功时自动重置错误计数

### 4. 改进的错误处理

更精确的错误分类和状态码：

- **503 Service Unavailable**：连接不可用或中断
- **504 Gateway Timeout**：服务器响应超时
- **500 Internal Server Error**：其他内部错误

### 5. 按需健康检查端点（优化后）

每个 MCP 服务器都会自动添加 `/health` 端点，仅在用户请求时执行检查：

```bash
# 按需检查服务器健康状态
curl http://localhost:8000/health
```

优化后的响应示例：
```json
{
  "status": "healthy",
  "connection_name": "SSE-http://127.0.0.1:8001/sse",
  "message": "MCP服务器连接正常",
  "details": {
    "error_count": 0,
    "last_error": null,
    "last_check": 1701234567.89,
    "check_type": "on_demand"
  }
}
```

## 使用方法

### 1. 基本使用

无需额外配置，新的连接管理功能会自动启用：

```bash
# SSE 服务器
mcpo --port 8000 --server-type "sse" -- http://127.0.0.1:8001/sse

# Streamable HTTP 服务器
mcpo --port 8000 --server-type "streamable_http" -- http://127.0.0.1:8002/mcp
```

### 2. 配置文件使用

在配置文件中可以自定义连接参数（功能计划中）：

```json
{
  "mcpServers": {
    "my_server": {
      "type": "sse",
      "url": "http://127.0.0.1:8001/sse",
      "connection_settings": {
        "retry_attempts": 5,
        "retry_delay": 3.0,
        "connection_timeout": 45.0,
        "health_check_interval": 30
      }
    }
  }
}
```

## 日志输出

新的连接管理功能会产生详细的日志输出：

```
INFO - 尝试连接到 SSE MCP Server (http://127.0.0.1:8001/sse) (第 1/3 次)
INFO - 正在初始化 SSE-http://127.0.0.1:8001/sse 的会话...
INFO - 服务器信息: TestServer v1.0.0
INFO - 正在获取 SSE-http://127.0.0.1:8001/sse 的工具列表...
INFO - 发现 3 个工具: ['tool1', 'tool2', 'tool3']
INFO - 已注册连接: SSE-http://127.0.0.1:8001/sse
INFO - 已启动连接 SSE-http://127.0.0.1:8001/sse 的健康监控
INFO - 成功为 SSE-http://127.0.0.1:8001/sse 创建了 3 个动态端点
```

## 错误处理示例

### 连接失败时的重试

```
INFO - 尝试连接到 SSE MCP Server (http://127.0.0.1:8001/sse) (第 1/3 次)
WARNING - 连接 SSE MCP Server (http://127.0.0.1:8001/sse) 失败 (第 1/3 次): Connection refused
INFO - 等待 2.0 秒后重试...
INFO - 尝试连接到 SSE MCP Server (http://127.0.0.1:8001/sse) (第 2/3 次)
INFO - 连接成功！
```

### 运行时连接问题

```
WARNING - 连接 SSE-http://127.0.0.1:8001/sse 健康检查失败: Connection lost
ERROR - 检测到连接 SSE-http://127.0.0.1:8001/sse 不健康
```

### API 调用时的错误响应

```json
{
  "detail": {
    "message": "MCP服务器连接问题: Connection timeout",
    "error": "Connection timeout after 30 seconds"
  }
}
```

## 最佳实践

1. **监控健康检查端点**：定期检查 `/health` 端点以监控连接状态
2. **合理设置超时**：根据网络环境调整连接超时时间
3. **查看日志**：关注连接相关的日志输出，及时发现问题
4. **处理 503/504 错误**：客户端应该能够处理这些临时性错误并重试

## 故障排除

### 常见问题

1. **连接一直失败**
   - 检查 MCP 服务器是否正在运行
   - 验证 URL 和端口是否正确
   - 检查网络连接

2. **频繁的健康检查失败**
   - 检查服务器负载
   - 考虑增加健康检查间隔
   - 检查网络稳定性

3. **超时错误**
   - 增加连接超时时间
   - 检查服务器响应时间
   - 优化网络配置

### 调试技巧

1. **启用详细日志**：
   ```bash
   mcpo --log-level debug --port 8000 --server-type "sse" -- http://127.0.0.1:8001/sse
   ```

2. **检查健康状态**：
   ```bash
   curl -v http://localhost:8000/health
   ```

3. **监控连接**：
   ```bash
   # 持续监控健康状态
   watch -n 5 'curl -s http://localhost:8000/health | jq'
   ```

## 技术实现

### 核心组件

1. **ConnectionManager**：管理所有 MCP 服务器连接
2. **retry_connection()**：实现连接重试逻辑
3. **create_connection_with_timeout()**：添加超时控制
4. **健康监控任务**：后台监控连接状态

### 架构改进

- 异步连接管理
- 指数退避重试策略
- 连接状态实时监控
- 详细的错误分类和处理

这些改进大大提高了 MCPO 在不稳定网络环境下的可靠性和用户体验。
