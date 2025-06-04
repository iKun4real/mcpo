# 提交说明翻译

## ec5bc79f3df752fc0fcdc496005b1b15275f0877

**原英文提交信息**: `Add Streamable HTTP Headers Support`

**中文翻译**: `🔐 功能: 添加StreamableHTTP自定义请求头支持`

### 详细说明

此提交添加了对SSE和StreamableHTTP MCP服务器的自定义请求头支持，主要功能包括：

#### ✨ 新增功能
- **CLI支持**: 使用 `--header "Key: Value"` 或 `-H "Key: Value"` 添加自定义请求头
- **配置文件支持**: 在服务器配置中添加 `"headers": {"Key": "Value"}`
- **多请求头支持**: CLI和配置文件都支持多个请求头
- **错误处理**: 对无效请求头格式进行适当的错误处理

#### 🛠️ 技术改进
- 扩展了CLI参数解析，支持 `--header` 和 `-H` 选项
- 修改了SSE和StreamableHTTP客户端调用，传递自定义请求头
- 更新了配置文件解析逻辑，支持headers字段
- 添加了请求头格式验证和错误提示

#### 📁 文件变更
- `src/mcpo/__init__.py`: 添加CLI请求头参数解析
- `src/mcpo/main.py`: 集成请求头到客户端连接
- `README.md`: 更新使用文档和示例
- `CHANGELOG.md`: 添加功能变更记录
- `example_config_with_headers.json`: 新增配置示例文件
- `example_usage.md`: 新增详细使用说明文档

#### 🎯 使用场景
- **身份验证**: 支持Bearer Token、API Key等认证方式
- **自定义头**: 支持User-Agent、Content-Type等自定义请求头
- **企业集成**: 满足企业级MCP服务器的认证需求

#### 📊 影响范围
- **新增**: 259行代码
- **删除**: 498行代码（主要是删除了GitHub相关配置文件）
- **净变化**: 支持自定义请求头功能，简化项目结构

这个功能大大增强了MCPO与需要身份验证的MCP服务器的兼容性，特别是在企业环境中的应用。
