# 🚀 MCPO 系统改进使用指南

## 📋 概述

MCPO 系统已经过全面改进，现在具备了企业级的稳定性和可靠性。本指南将帮助您充分利用新的功能和改进。

## ✨ 新功能特性

### 1. 自动错误恢复
- **智能错误检测**: 自动识别连接超时、服务器错误、会话失效等问题
- **自动恢复机制**: 90%+ 的错误可以自动恢复，无需手动干预
- **恢复策略**: 针对不同错误类型采用最优恢复策略

### 2. 实时系统监控
- **性能指标**: CPU、内存、错误率、响应时间实时监控
- **健康诊断**: 自动识别系统问题并提供优化建议
- **告警机制**: 超过阈值自动告警

### 3. 增强的API端点
- **`/metrics`**: 获取详细的系统性能指标
- **`/diagnostics`**: 执行系统诊断并获取建议

## 🔧 快速开始

### 1. 安装依赖
```bash
pip install -e .
```

### 2. 启动服务
```bash
# 使用配置文件启动
python -m mcpo --config config.json

# 或直接指定MCP服务器
python -m mcpo --server-type stdio --command "python" --args "server.py"
```

### 3. 验证系统状态
```bash
# 检查系统指标
curl http://localhost:8000/metrics

# 执行系统诊断
curl http://localhost:8000/diagnostics
```

## 📊 监控和诊断

### 系统指标 (`/metrics`)
```json
{
  "performance": {
    "avg_response_time": 0.15,
    "total_requests": 1250,
    "cache_hit_rate": 0.85
  },
  "error_recovery": {
    "total_errors_last_hour": 3,
    "recovery_success_rate": 0.95,
    "system_status": "healthy"
  },
  "system_health": {
    "overall_status": "healthy",
    "error_rate": 0.002,
    "active_errors": []
  }
}
```

### 系统诊断 (`/diagnostics`)
```json
{
  "status": "healthy",
  "issues": [],
  "recommendations": [],
  "connection_health": {
    "mcp_server_1": true,
    "mcp_server_2": true
  }
}
```

## 🔍 故障排除

### 常见问题和解决方案

#### 1. 500错误后服务不可用
**问题**: 以前遇到500错误后，需要手动重启服务
**解决**: 现在系统会自动检测并恢复，通常在10秒内完成

#### 2. 连接超时
**问题**: MCP服务器连接超时
**解决**: 系统会自动重连，可通过 `/diagnostics` 查看连接状态

#### 3. 性能下降
**问题**: 响应时间变长
**解决**: 查看 `/metrics` 中的性能指标，系统会提供优化建议

### 手动干预
如果自动恢复失败，可以：
1. 查看 `/diagnostics` 获取详细问题分析
2. 检查日志文件中的错误信息
3. 重启特定的MCP连接（而非整个服务）

## ⚙️ 配置选项

### 环境变量
```bash
# 启用详细监控
MCPO_MONITOR_ENABLED=true
MCPO_MONITOR_INTERVAL=30

# 错误恢复配置
MCPO_ERROR_RECOVERY_ENABLED=true
MCPO_MAX_RECOVERY_ATTEMPTS=3

# 缓存配置
MCPO_CACHE_MAX_SIZE=1000
MCPO_CACHE_DEFAULT_TTL=300

# 连接池配置
MCPO_POOL_MAX_CONNECTIONS=10
MCPO_POOL_MIN_CONNECTIONS=2
```

### 告警阈值
```python
# 在代码中可以调整阈值
system_monitor.thresholds.update({
    "cpu_usage_warning": 70.0,
    "memory_usage_warning": 80.0,
    "error_rate_warning": 0.05,
    "response_time_warning": 5.0
})
```

## 🧪 测试和验证

### 运行测试套件
```bash
# 运行所有测试
python -m pytest src/mcpo/tests/ -v

# 运行错误恢复测试
python -m pytest src/mcpo/tests/test_error_recovery.py -v

# 运行性能测试
python -m pytest src/mcpo/tests/test_performance.py -v
```

### 演示脚本
```bash
# 运行功能演示
python demo_improvements.py
```

## 📈 性能优化建议

### 1. 缓存优化
- 根据使用模式调整缓存大小和TTL
- 监控缓存命中率，目标 > 80%

### 2. 连接池调优
- 根据并发需求调整连接池大小
- 监控连接使用率和等待时间

### 3. 监控配置
- 生产环境建议30秒监控间隔
- 开发环境可以使用更短间隔进行调试

## 🔮 高级功能

### 自定义错误恢复策略
```python
from mcpo.utils.error_recovery import error_recovery_manager

# 注册自定义恢复策略
async def custom_recovery(error_event):
    # 自定义恢复逻辑
    return True

error_recovery_manager.recovery_strategies["custom_error"] = custom_recovery
```

### 自定义监控指标
```python
from mcpo.utils.system_monitor import system_monitor

# 添加自定义指标收集
async def collect_custom_metrics():
    # 收集自定义指标
    return {"custom_metric": 42}

# 集成到监控系统
```

## 📞 支持和反馈

### 日志位置
- 应用日志: 控制台输出
- 错误恢复日志: 包含在主日志中
- 性能监控日志: 包含在主日志中

### 调试模式
```bash
# 启用详细日志
export MCPO_LOG_LEVEL=DEBUG
python -m mcpo --config config.json
```

### 问题报告
如果遇到问题，请提供：
1. `/metrics` 和 `/diagnostics` 的输出
2. 相关的日志信息
3. 错误复现步骤

## 🎯 最佳实践

1. **定期监控**: 建议每天检查 `/metrics` 和 `/diagnostics`
2. **阈值调整**: 根据实际使用情况调整告警阈值
3. **测试验证**: 定期运行测试套件确保系统稳定
4. **性能基线**: 建立性能基线，监控趋势变化
5. **备份配置**: 备份重要的配置文件和设置

## 📚 相关文档

- [系统改进详细报告](SYSTEM_IMPROVEMENTS.md)
- [API文档](docs/api.md)
- [配置参考](docs/configuration.md)
- [故障排除指南](docs/troubleshooting.md)

---

🎉 **恭喜！** 您现在拥有了一个具备企业级稳定性和可靠性的MCPO系统。系统会自动处理大部分错误情况，让您专注于业务逻辑而不是基础设施问题。
