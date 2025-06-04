import json
import os
import logging
import socket
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from starlette.routing import Mount

logger = logging.getLogger(__name__)


from mcpo.utils.main import get_model_fields, get_tool_handler
from mcpo.utils.auth import get_verify_api_key, APIKeyMiddleware


# 连接配置常量
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_CONNECTION_TIMEOUT = 30.0
DEFAULT_SSE_READ_TIMEOUT = 60.0


async def retry_connection(
    connection_func,
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay: float = DEFAULT_RETRY_DELAY,
    connection_name: str = "MCP Server"
):
    """
    重试连接函数，用于处理连接失败的情况
    """
    last_exception = None

    for attempt in range(max_attempts):
        try:
            logger.info(f"尝试连接到 {connection_name} (第 {attempt + 1}/{max_attempts} 次)")
            return await connection_func()
        except Exception as e:
            last_exception = e
            logger.warning(f"连接 {connection_name} 失败 (第 {attempt + 1}/{max_attempts} 次): {str(e)}")

            if attempt < max_attempts - 1:
                logger.info(f"等待 {delay} 秒后重试...")
                await asyncio.sleep(delay)
                delay *= 1.5  # 指数退避
            else:
                logger.error(f"所有连接尝试都失败了，放弃连接到 {connection_name}")

    raise last_exception


async def create_connection_with_timeout(connection_func, timeout: float = DEFAULT_CONNECTION_TIMEOUT):
    """
    带超时的连接创建函数
    """
    try:
        return await asyncio.wait_for(connection_func(), timeout=timeout)
    except asyncio.TimeoutError:
        raise Exception(f"连接超时 ({timeout} 秒)")


class ConnectionManager:
    """
    连接管理器，用于管理MCP服务器连接状态
    支持多服务器独立管理
    """
    def __init__(self, manager_id: str = "default"):
        self.manager_id = manager_id
        self.connections = {}
        self.connection_status = {}  # 缓存连接状态

    def register_connection(self, name: str, session: ClientSession):
        """注册连接"""
        self.connections[name] = session
        self.connection_status[name] = {
            "status": "healthy",
            "last_error": None,
            "error_count": 0,
            "last_check": asyncio.get_event_loop().time()
        }
        logger.info(f"[{self.manager_id}] 已注册连接: {name}")

    def unregister_connection(self, name: str):
        """注销连接"""
        if name in self.connections:
            del self.connections[name]
            logger.info(f"[{self.manager_id}] 已注销连接: {name}")

        if name in self.connection_status:
            del self.connection_status[name]

    async def check_connection_health(self, name: str, session: ClientSession):
        """按需检查连接健康状态（仅在用户请求时调用）"""
        try:
            # 尝试列出工具来检查连接是否正常
            await session.list_tools()

            # 更新连接状态
            if name in self.connection_status:
                self.connection_status[name].update({
                    "status": "healthy",
                    "last_error": None,
                    "last_check": asyncio.get_event_loop().time()
                })

            return True
        except Exception as e:
            logger.warning(f"[{self.manager_id}] 连接 {name} 健康检查失败: {str(e)}")

            # 更新连接状态
            if name in self.connection_status:
                self.connection_status[name].update({
                    "status": "unhealthy",
                    "last_error": str(e),
                    "last_check": asyncio.get_event_loop().time()
                })

            return False

    def record_connection_error(self, name: str, error: str):
        """记录连接错误（在API调用失败时调用）"""
        if name in self.connection_status:
            self.connection_status[name]["error_count"] += 1
            self.connection_status[name]["last_error"] = error
            self.connection_status[name]["status"] = "error"
            logger.warning(f"[{self.manager_id}] 连接 {name} 发生错误: {error} (错误次数: {self.connection_status[name]['error_count']})")

    def record_connection_success(self, name: str):
        """记录连接成功（在API调用成功时调用）"""
        if name in self.connection_status:
            # 如果之前有错误，现在成功了，重置错误计数
            if self.connection_status[name]["error_count"] > 0:
                logger.info(f"[{self.manager_id}] 连接 {name} 已恢复正常")

            self.connection_status[name].update({
                "status": "healthy",
                "error_count": 0,
                "last_error": None,
                "last_check": asyncio.get_event_loop().time()
            })

    def get_connection_status(self, name: str):
        """获取连接状态信息"""
        return self.connection_status.get(name, {"status": "unknown"})


# 全局连接管理器
connection_manager = ConnectionManager()


async def create_dynamic_endpoints(app: FastAPI, api_dependency=None, connection_name: str = "Unknown", connection_manager: ConnectionManager = None):
    session: ClientSession = app.state.session
    if not session:
        raise ValueError("Session is not initialized in the app state.")

    # 如果没有提供连接管理器，为这个应用创建一个独立的
    if connection_manager is None:
        connection_manager = ConnectionManager(manager_id=connection_name)
        app.state.connection_manager = connection_manager

    try:
        logger.info(f"正在初始化 {connection_name} 的会话...")
        result = await session.initialize()
        server_info = getattr(result, "serverInfo", None)
        if server_info:
            app.title = server_info.name or app.title
            app.description = (
                f"{server_info.name} MCP Server" if server_info.name else app.description
            )
            app.version = server_info.version or app.version
            logger.info(f"服务器信息: {server_info.name} v{server_info.version}")

        logger.info(f"正在获取 {connection_name} 的工具列表...")
        tools_result = await session.list_tools()
        tools = tools_result.tools
        logger.info(f"发现 {len(tools)} 个工具: {[tool.name for tool in tools]}")

        # 注册连接到连接管理器
        connection_manager.register_connection(connection_name, session)

        for tool in tools:
            endpoint_name = tool.name
            endpoint_description = tool.description

            inputSchema = tool.inputSchema
            outputSchema = getattr(tool, "outputSchema", None)

            form_model_fields = get_model_fields(
                f"{endpoint_name}_form_model",
                inputSchema.get("properties", {}),
                inputSchema.get("required", []),
                inputSchema.get("$defs", {}),
            )

            response_model_fields = None
            if outputSchema:
                response_model_fields = get_model_fields(
                    f"{endpoint_name}_response_model",
                    outputSchema.get("properties", {}),
                    outputSchema.get("required", []),
                    outputSchema.get("$defs", {}),
                )

            tool_handler = get_tool_handler(
                session,
                endpoint_name,
                form_model_fields,
                response_model_fields,
                connection_name,
                connection_manager,
            )

            app.post(
                f"/{endpoint_name}",
                summary=endpoint_name.replace("_", " ").title(),
                description=endpoint_description,
                response_model_exclude_none=True,
                dependencies=[Depends(api_dependency)] if api_dependency else [],
            )(tool_handler)

        logger.info(f"成功为 {connection_name} 创建了 {len(tools)} 个动态端点")

        # 添加健康检查端点
        @app.get("/health", summary="健康检查", description="检查MCP服务器连接状态")
        async def health_check():
            """检查MCP服务器连接健康状态"""
            try:
                # 获取缓存的连接状态
                cached_status = connection_manager.get_connection_status(connection_name)

                # 执行实时健康检查
                is_healthy = await connection_manager.check_connection_health(connection_name, session)

                # 获取更新后的状态
                current_status = connection_manager.get_connection_status(connection_name)

                return {
                    "status": "healthy" if is_healthy else "unhealthy",
                    "connection_name": connection_name,
                    "message": "MCP服务器连接正常" if is_healthy else "MCP服务器连接异常",
                    "details": {
                        "error_count": current_status.get("error_count", 0),
                        "last_error": current_status.get("last_error"),
                        "last_check": current_status.get("last_check"),
                        "check_type": "on_demand"  # 表示这是按需检查
                    }
                }
            except Exception as e:
                connection_manager.record_connection_error(connection_name, str(e))
                return {
                    "status": "error",
                    "connection_name": connection_name,
                    "message": f"健康检查失败: {str(e)}",
                    "details": {
                        "error_count": connection_manager.get_connection_status(connection_name).get("error_count", 0),
                        "last_error": str(e),
                        "check_type": "on_demand"
                    }
                }

        # 添加性能监控端点
        @app.get("/metrics", summary="性能指标", description="获取性能监控数据")
        async def get_metrics():
            """获取性能监控指标"""
            try:
                from mcpo.utils.performance import performance_monitor, concurrency_limiter
                from mcpo.utils.cache import cache_manager

                return {
                    "performance": performance_monitor.get_metrics(),
                    "concurrency": concurrency_limiter.get_stats(),
                    "cache": cache_manager.get_all_stats(),
                    "connection": connection_manager.get_connection_status(connection_name),
                    "timestamp": time.time()
                }
            except Exception as e:
                logger.error(f"获取性能指标失败: {str(e)}")
                return {
                    "error": str(e),
                    "timestamp": time.time()
                }

    except Exception as e:
        logger.error(f"创建 {connection_name} 的动态端点时发生错误: {str(e)}")
        # 清理连接管理器中的记录
        connection_manager.unregister_connection(connection_name)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_type = getattr(app.state, "server_type", "stdio")
    command = getattr(app.state, "command", None)
    args = getattr(app.state, "args", [])
    env = getattr(app.state, "env", {})
    headers = getattr(app.state, "headers", {})

    args = args if isinstance(args, list) else [args]
    api_dependency = getattr(app.state, "api_dependency", None)

    if (server_type == "stdio" and not command) or (
        server_type == "sse" and not args[0]
    ):
        # Main app lifespan (when config_path is provided)
        async with AsyncExitStack() as stack:
            for route in app.routes:
                if isinstance(route, Mount) and isinstance(route.app, FastAPI):
                    await stack.enter_async_context(
                        route.app.router.lifespan_context(route.app),  # noqa
                    )
            yield
    else:
        if server_type == "stdio":
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**env},
            )

            async def create_stdio_connection():
                return stdio_client(server_params)

            try:
                connection_context = await retry_connection(
                    create_stdio_connection,
                    connection_name=f"Stdio MCP Server ({command})"
                )
                async with connection_context as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        app.state.session = session
                        await create_dynamic_endpoints(
                            app,
                            api_dependency=api_dependency,
                            connection_name=f"Stdio-{command}",
                            connection_manager=getattr(app.state, 'connection_manager', None)
                        )
                        yield
            except Exception as e:
                logger.error(f"Stdio MCP服务器连接失败: {str(e)}")
                raise

        if server_type == "sse":
            url = args[0]

            async def create_sse_connection():
                return sse_client(
                    url=url,
                    headers=headers,
                    sse_read_timeout=DEFAULT_SSE_READ_TIMEOUT
                )

            try:
                connection_context = await retry_connection(
                    lambda: create_connection_with_timeout(create_sse_connection),
                    connection_name=f"SSE MCP Server ({url})"
                )
                async with connection_context as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        app.state.session = session
                        await create_dynamic_endpoints(
                            app,
                            api_dependency=api_dependency,
                            connection_name=f"SSE-{url}",
                            connection_manager=getattr(app.state, 'connection_manager', None)
                        )
                        yield
            except Exception as e:
                logger.error(f"SSE MCP服务器连接失败: {str(e)}")
                raise

        if server_type == "streamablehttp" or server_type == "streamable_http":
            # Ensure URL has trailing slash to avoid redirects
            url = args[0]
            if not url.endswith("/"):
                url = f"{url}/"

            # 使用弹性连接管理器
            from mcpo.utils.reconnect_manager import resilient_streamable_connection, reconnect_manager

            try:
                async with resilient_streamable_connection(url, headers, app) as session:
                    app.state.session = session
                    connection_name = f"StreamableHTTP-{url}"

                    # 弹性连接已经处理了重连管理器的注册，这里只需要创建端点
                    await create_dynamic_endpoints(
                        app,
                        api_dependency=api_dependency,
                        connection_name=connection_name,
                        connection_manager=getattr(app.state, 'connection_manager', None)
                    )
                    yield

            except Exception as e:
                logger.error(f"StreamableHTTP MCP服务器连接失败: {str(e)}")
                raise


async def run(
    host: str = "127.0.0.1",
    port: int = 8000,
    api_key: Optional[str] = "",
    cors_allow_origins=["*"],
    **kwargs,
):
    # Server API Key
    api_dependency = get_verify_api_key(api_key) if api_key else None
    strict_auth = kwargs.get("strict_auth", False)

    # MCP Server
    server_type = kwargs.get(
        "server_type"
    )  # "stdio", "sse", or "streamablehttp" ("streamable_http" is also accepted)
    server_command = kwargs.get("server_command")

    # Custom headers for SSE/Streamable HTTP
    custom_headers = kwargs.get("headers", {})

    # MCP Config
    config_path = kwargs.get("config_path")

    # mcpo server
    name = kwargs.get("name") or "MCP OpenAPI Proxy"
    description = (
        kwargs.get("description") or "Automatically generated API from MCP Tool Schemas"
    )
    version = kwargs.get("version") or "1.0"

    ssl_certfile = kwargs.get("ssl_certfile")
    ssl_keyfile = kwargs.get("ssl_keyfile")
    path_prefix = kwargs.get("path_prefix") or "/"

    # Configure basic logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger.info("Starting MCPO Server...")
    logger.info(f"  Name: {name}")
    logger.info(f"  Version: {version}")
    logger.info(f"  Description: {description}")
    logger.info(f"  Hostname: {socket.gethostname()}")
    logger.info(f"  Port: {port}")
    logger.info(f"  API Key: {'Provided' if api_key else 'Not Provided'}")
    logger.info(f"  CORS Allowed Origins: {cors_allow_origins}")
    if ssl_certfile:
        logger.info(f"  SSL Certificate File: {ssl_certfile}")
    if ssl_keyfile:
        logger.info(f"  SSL Key File: {ssl_keyfile}")
    logger.info(f"  Path Prefix: {path_prefix}")

    main_app = FastAPI(
        title=name,
        description=description,
        version=version,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        lifespan=lifespan,
    )

    main_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add middleware to protect also documentation and spec
    if api_key and strict_auth:
        main_app.add_middleware(APIKeyMiddleware, api_key=api_key)

    if server_type == "sse":
        logger.info(
            f"Configuring for a single SSE MCP Server with URL {server_command[0]}"
        )
        main_app.state.server_type = "sse"
        main_app.state.args = server_command[0]  # Expects URL as the first element
        main_app.state.headers = custom_headers
        main_app.state.api_dependency = api_dependency
    elif server_type == "streamablehttp" or server_type == "streamable_http":
        logger.info(
            f"Configuring for a single StreamableHTTP MCP Server with URL {server_command[0]}"
        )
        main_app.state.server_type = "streamablehttp"
        main_app.state.args = server_command[0]  # Expects URL as the first element
        main_app.state.headers = custom_headers
        main_app.state.api_dependency = api_dependency
    elif server_command:  # This handles stdio
        logger.info(
            f"Configuring for a single Stdio MCP Server with command: {' '.join(server_command)}"
        )
        main_app.state.server_type = "stdio"  # Explicitly set type
        main_app.state.command = server_command[0]
        main_app.state.args = server_command[1:]
        main_app.state.env = os.environ.copy()
        main_app.state.api_dependency = api_dependency
    elif config_path:
        logger.info(f"Loading MCP server configurations from: {config_path}")
        with open(config_path, "r") as f:
            config_data = json.load(f)

        mcp_servers = config_data.get("mcpServers", {})
        if not mcp_servers:
            logger.error(f"No 'mcpServers' found in config file: {config_path}")
            raise ValueError("No 'mcpServers' found in config file.")

        logger.info("Configured MCP Servers:")
        for server_name_cfg, server_cfg_details in mcp_servers.items():
            if server_cfg_details.get("command"):
                args_info = (
                    f" with args: {server_cfg_details['args']}"
                    if server_cfg_details.get("args")
                    else ""
                )
                logger.info(
                    f"  Configuring Stdio MCP Server '{server_name_cfg}' with command: {server_cfg_details['command']}{args_info}"
                )
            elif server_cfg_details.get("type") == "sse" and server_cfg_details.get(
                "url"
            ):
                logger.info(
                    f"  Configuring SSE MCP Server '{server_name_cfg}' with URL: {server_cfg_details['url']}"
                )
            elif (
                server_cfg_details.get("type") == "streamablehttp"
                or server_cfg_details.get("type") == "streamable_http"
            ) and server_cfg_details.get("url"):
                logger.info(
                    f"  Configuring StreamableHTTP MCP Server '{server_name_cfg}' with URL: {server_cfg_details['url']}"
                )
            elif server_cfg_details.get("url"):  # Fallback for old SSE config
                logger.info(
                    f"  Configuring SSE (fallback) MCP Server '{server_name_cfg}' with URL: {server_cfg_details['url']}"
                )
            else:
                logger.warning(
                    f"  Unknown configuration for MCP server: {server_name_cfg}"
                )

        main_app.description += "\n\n- **available tools**："
        for server_name, server_cfg in mcp_servers.items():
            # 为每个子应用创建独立的lifespan函数
            def create_sub_lifespan(server_name_param):
                @asynccontextmanager
                async def sub_lifespan(app: FastAPI):
                    # 为这个子应用创建独立的连接管理器
                    app.state.connection_manager = ConnectionManager(manager_id=server_name_param)

                    # 调用原始的lifespan逻辑
                    async with lifespan(app):
                        yield
                return sub_lifespan

            sub_app = FastAPI(
                title=f"{server_name}",
                description=f"{server_name} MCP Server\n\n- [back to tool list](/docs)",
                version="1.0",
                lifespan=create_sub_lifespan(server_name),
            )

            sub_app.add_middleware(
                CORSMiddleware,
                allow_origins=cors_allow_origins or ["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

            if server_cfg.get("command"):
                # stdio
                sub_app.state.server_type = "stdio"
                sub_app.state.command = server_cfg["command"]
                sub_app.state.args = server_cfg.get("args", [])
                sub_app.state.env = {**os.environ, **server_cfg.get("env", {})}

            server_config_type = server_cfg.get("type")
            if server_config_type == "sse" and server_cfg.get("url"):
                sub_app.state.server_type = "sse"
                sub_app.state.args = server_cfg["url"]
                sub_app.state.headers = server_cfg.get("headers", {})
            elif (
                server_config_type == "streamablehttp"
                or server_config_type == "streamable_http"
            ) and server_cfg.get("url"):
                # Store the URL with trailing slash to avoid redirects
                url = server_cfg["url"]
                if not url.endswith("/"):
                    url = f"{url}/"
                sub_app.state.server_type = "streamablehttp"
                sub_app.state.args = url
                sub_app.state.headers = server_cfg.get("headers", {})
            elif not server_config_type and server_cfg.get(
                "url"
            ):  # Fallback for old SSE config
                sub_app.state.server_type = "sse"
                sub_app.state.args = server_cfg["url"]
                sub_app.state.headers = server_cfg.get("headers", {})

            # Add middleware to protect also documentation and spec
            if api_key and strict_auth:
                sub_app.add_middleware(APIKeyMiddleware, api_key=api_key)

            sub_app.state.api_dependency = api_dependency

            main_app.mount(f"{path_prefix}{server_name}", sub_app)
            main_app.description += f"\n    - [{server_name}](/{server_name}/docs)"
    else:
        logger.error("MCPO server_command or config_path must be provided.")
        raise ValueError("You must provide either server_command or config.")

    logger.info("Uvicorn server starting...")
    config = uvicorn.Config(
        app=main_app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        log_level="info",
    )
    server = uvicorn.Server(config)

    await server.serve()
