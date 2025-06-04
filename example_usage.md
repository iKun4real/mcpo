# MCPO 自定义请求头使用示例

本文档展示如何在 MCPO 中使用自定义请求头功能，特别是对于需要身份验证的 SSE 和 Streamable HTTP MCP 服务器。

## CLI 使用方式

### 单个请求头

```bash
# SSE 服务器带 Authorization 头
mcpo --port 8000 --server-type "sse" \
  --header "Authorization: Bearer your-secret-token" \
  -- http://127.0.0.1:8001/sse

# Streamable HTTP 服务器带 API Key
mcpo --port 8000 --server-type "streamable_http" \
  --header "X-API-Key: your-api-key" \
  -- http://127.0.0.1:8002/mcp
```

### 多个请求头

```bash
# 多个自定义头
mcpo --port 8000 --server-type "sse" \
  --header "Authorization: Bearer your-token" \
  --header "X-API-Key: your-api-key" \
  --header "User-Agent: mcpo/0.0.14" \
  --header "Accept: application/json" \
  -- http://127.0.0.1:8001/sse

# 使用短选项 -H
mcpo --port 8000 --server-type "streamable_http" \
  -H "Authorization: Bearer your-token" \
  -H "X-Custom-Header: custom-value" \
  -H "Content-Type: application/json" \
  -- http://127.0.0.1:8002/mcp
```

## 配置文件使用方式

创建配置文件 `config.json`：

```json
{
  "mcpServers": {
    "authenticated_sse": {
      "type": "sse",
      "url": "http://127.0.0.1:8001/sse",
      "headers": {
        "Authorization": "Bearer your-secret-token",
        "X-API-Key": "your-api-key",
        "User-Agent": "mcpo/0.0.14"
      }
    },
    "authenticated_streamable": {
      "type": "streamable_http",
      "url": "http://127.0.0.1:8002/mcp",
      "headers": {
        "Authorization": "Bearer your-secret-token",
        "X-Client-ID": "mcpo-client",
        "Accept": "application/json"
      }
    },
    "public_server": {
      "type": "sse",
      "url": "http://127.0.0.1:8003/sse"
    }
  }
}
```

然后运行：

```bash
mcpo --port 8000 --config config.json
```

## 常见使用场景

### 1. Bearer Token 认证

```bash
mcpo --port 8000 --server-type "sse" \
  --header "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
  -- https://api.example.com/mcp/sse
```

### 2. API Key 认证

```bash
mcpo --port 8000 --server-type "streamable_http" \
  --header "X-API-Key: sk-1234567890abcdef" \
  -- https://api.example.com/mcp
```

### 3. 基本认证

```bash
mcpo --port 8000 --server-type "sse" \
  --header "Authorization: Basic dXNlcm5hbWU6cGFzc3dvcmQ=" \
  -- https://api.example.com/mcp/sse
```

### 4. 自定义用户代理

```bash
mcpo --port 8000 --server-type "streamable_http" \
  --header "User-Agent: MyApp/1.0 (mcpo/0.0.14)" \
  -- http://127.0.0.1:8002/mcp
```

## 错误处理

如果请求头格式不正确，MCPO 会显示警告并跳过该头：

```bash
# 错误格式示例
mcpo --port 8000 --server-type "sse" \
  --header "InvalidHeader" \
  -- http://127.0.0.1:8001/sse

# 输出: Warning: Invalid header format 'InvalidHeader'. Expected 'Key: Value'
```

正确格式应该是 `Key: Value`，注意冒号后面的空格。

## 安全注意事项

1. **不要在命令行中暴露敏感信息**：使用配置文件而不是 CLI 参数来传递敏感的认证信息
2. **使用环境变量**：在配置文件中可以引用环境变量来避免硬编码敏感信息
3. **HTTPS**：对于生产环境，始终使用 HTTPS 来保护传输中的认证信息

## 测试连接

你可以使用以下命令测试连接是否正常工作：

```bash
# 测试不带认证的连接
curl http://localhost:8000/docs

# 如果服务器正常运行，你应该看到 OpenAPI 文档
```
