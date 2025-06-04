"""
增强的重连管理器
专门处理MCP服务器连接失败和自动重连
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Callable, Any
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


class ReconnectManager:
    """
    重连管理器，处理连接失败和自动重连
    """
    
    def __init__(self):
        self.connections: Dict[str, ClientSession] = {}
        self.connection_factories: Dict[str, Callable] = {}
        self.connection_configs: Dict[str, Dict[str, Any]] = {}
        self.reconnect_locks: Dict[str, asyncio.Lock] = {}
        self.connection_status: Dict[str, Dict[str, Any]] = {}
        self.apps: Dict[str, Any] = {}
        
    def register_connection(self, 
                          name: str, 
                          session: ClientSession,
                          connection_factory: Callable,
                          config: Dict[str, Any],
                          app: Any = None):
        """注册连接和重连信息"""
        self.connections[name] = session
        self.connection_factories[name] = connection_factory
        self.connection_configs[name] = config
        self.reconnect_locks[name] = asyncio.Lock()
        self.connection_status[name] = {
            "status": "healthy",
            "last_error": None,
            "error_count": 0,
            "reconnect_attempts": 0,
            "last_check": time.time(),
            "last_reconnect": 0
        }
        if app:
            self.apps[name] = app
        logger.info(f"已注册可重连连接: {name}")
    
    def unregister_connection(self, name: str):
        """注销连接"""
        for dict_obj in [self.connections, self.connection_factories, 
                        self.connection_configs, self.reconnect_locks,
                        self.connection_status, self.apps]:
            dict_obj.pop(name, None)
        logger.info(f"已注销连接: {name}")
    
    def record_error(self, name: str, error: str):
        """记录连接错误"""
        if name in self.connection_status:
            status = self.connection_status[name]
            status["error_count"] += 1
            status["last_error"] = error
            status["status"] = "error"
            status["last_check"] = time.time()
            logger.warning(f"连接 {name} 错误: {error} (错误次数: {status['error_count']})")
    
    def record_success(self, name: str):
        """记录连接成功"""
        if name in self.connection_status:
            status = self.connection_status[name]
            if status["error_count"] > 0:
                logger.info(f"连接 {name} 已恢复正常")
            status.update({
                "status": "healthy",
                "error_count": 0,
                "last_error": None,
                "last_check": time.time()
            })
    
    def should_reconnect(self, name: str) -> bool:
        """判断是否应该尝试重连"""
        if name not in self.connection_status:
            return False
            
        status = self.connection_status[name]
        current_time = time.time()
        
        # 检查重连频率限制（最少间隔30秒）
        if current_time - status.get("last_reconnect", 0) < 30:
            return False
            
        # 检查重连次数限制
        if status.get("reconnect_attempts", 0) >= 5:
            return False
            
        # 检查错误次数
        if status.get("error_count", 0) >= 3:
            return True
            
        return False
    
    async def attempt_reconnect(self, name: str) -> bool:
        """尝试重连"""
        if name not in self.connection_factories:
            logger.warning(f"连接 {name} 未在重连管理器中注册，无法自动重连")
            return False
        
        # 使用锁防止并发重连
        async with self.reconnect_locks[name]:
            # 再次检查是否需要重连（可能其他协程已经重连成功）
            if self.connection_status[name]["status"] == "healthy":
                return True
            
            logger.info(f"开始重连 {name}...")
            
            # 更新重连状态
            status = self.connection_status[name]
            status["reconnect_attempts"] += 1
            status["last_reconnect"] = time.time()
            
            try:
                # 获取连接工厂和配置
                factory = self.connection_factories[name]
                config = self.connection_configs[name]
                
                # 尝试重新建立连接
                connection_context = await self._retry_connection(factory, name)
                
                # 测试连接
                async with connection_context as connection_result:
                    if len(connection_result) == 2:
                        reader, writer = connection_result
                    else:
                        reader, writer, _ = connection_result
                    
                    # 创建新的会话
                    async with ClientSession(reader, writer) as new_session:
                        # 测试会话是否正常
                        await new_session.list_tools()
                        
                        # 更新连接
                        self.connections[name] = new_session
                        
                        # 更新应用状态
                        if name in self.apps:
                            self.apps[name].state.session = new_session
                        
                        # 记录成功
                        self.record_success(name)
                        status["reconnect_attempts"] = 0  # 重置重连次数
                        
                        logger.info(f"成功重连 {name}")
                        return True
                        
            except Exception as e:
                logger.error(f"重连 {name} 失败: {str(e)}")
                self.record_error(name, f"重连失败: {str(e)}")
                return False
    
    async def _retry_connection(self, factory: Callable, name: str, max_attempts: int = 3):
        """重试连接"""
        last_exception = None
        
        for attempt in range(max_attempts):
            try:
                logger.debug(f"重连 {name} 尝试 {attempt + 1}/{max_attempts}")
                return await factory()
            except Exception as e:
                last_exception = e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)  # 指数退避
        
        raise last_exception
    
    async def get_healthy_session(self, name: str) -> Optional[ClientSession]:
        """获取健康的会话，如果不健康则尝试重连"""
        if name not in self.connections:
            return None

        session = self.connections[name]

        # 先进行实时健康检查
        try:
            await asyncio.wait_for(session.list_tools(), timeout=5.0)
            # 更新健康状态
            if name in self.connection_status:
                self.connection_status[name].update({
                    "status": "healthy",
                    "last_check": time.time(),
                    "last_error": None
                })
            return session
        except Exception as e:
            logger.warning(f"会话 {name} 健康检查失败: {str(e)}")
            self.record_error(name, f"会话健康检查失败: {str(e)}")

        # 如果不健康且应该重连，则尝试重连
        if self.should_reconnect(name):
            success = await self.attempt_reconnect(name)
            if success:
                return self.connections[name]

        return None

    async def refresh_connection_state(self, name: str) -> bool:
        """刷新连接状态，强制重新验证会话健康"""
        if name not in self.connections:
            logger.warning(f"连接 {name} 不存在，无法刷新状态")
            return False

        try:
            session = self.connections[name]
            # 执行健康检查
            await asyncio.wait_for(session.list_tools(), timeout=3.0)

            # 更新状态
            if name in self.connection_status:
                self.connection_status[name].update({
                    "status": "healthy",
                    "last_check": time.time(),
                    "last_error": None
                })

            logger.debug(f"连接状态刷新成功: {name}")
            return True

        except Exception as e:
            logger.warning(f"刷新连接状态失败 {name}: {str(e)}")
            self.record_error(name, f"状态刷新失败: {str(e)}")
            return False

    async def validate_all_connections(self) -> Dict[str, bool]:
        """验证所有连接的健康状态"""
        results = {}
        for name in list(self.connections.keys()):
            try:
                is_healthy = await self.refresh_connection_state(name)
                results[name] = is_healthy
            except Exception as e:
                logger.error(f"验证连接 {name} 时发生异常: {str(e)}")
                results[name] = False

        return results
    
    def get_status(self, name: str) -> Dict[str, Any]:
        """获取连接状态"""
        return self.connection_status.get(name, {"status": "unknown"})
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有连接状态"""
        return self.connection_status.copy()


# 全局重连管理器
reconnect_manager = ReconnectManager()


@asynccontextmanager
async def resilient_streamable_connection(url: str, headers: Dict[str, str] = None, app: Any = None):
    """
    弹性StreamableHTTP连接上下文管理器
    自动处理502等错误并重连
    """
    headers = headers or {}
    connection_name = f"StreamableHTTP-{url}"

    async def create_connection():
        return streamablehttp_client(url=url, headers=headers)

    max_attempts = 3
    last_exception = None

    for attempt in range(max_attempts):
        try:
            logger.info(f"尝试连接到 {url} (第 {attempt + 1}/{max_attempts} 次)")
            connection_context = await create_connection()

            async with connection_context as connection_result:
                reader, writer, _ = connection_result
                async with ClientSession(reader, writer) as session:
                    # 注册到重连管理器
                    reconnect_manager.register_connection(
                        connection_name,
                        session,
                        create_connection,
                        {"url": url, "headers": headers},
                        app
                    )

                    try:
                        yield session
                    finally:
                        reconnect_manager.unregister_connection(connection_name)
                        
        except Exception as e:
            last_exception = e
            logger.warning(f"连接 {url} 失败 (尝试 {attempt + 1}/{max_attempts}): {str(e)}")
            
            if attempt < max_attempts - 1:
                wait_time = 2 ** attempt
                logger.info(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"所有重连尝试失败，放弃连接到 {url}")
                raise last_exception


async def handle_connection_error(name: str, error: Exception) -> bool:
    """
    处理连接错误，如果是可恢复的错误则尝试重连
    返回True表示已处理（可能重连成功），False表示无法处理
    """
    error_str = str(error).lower()

    # 检查是否是可重连的错误
    recoverable_errors = [
        "502 bad gateway",
        "503 service unavailable",
        "504 gateway timeout",
        "524",  # Cloudflare timeout
        "520",  # Cloudflare web server error
        "521",  # Cloudflare web server is down
        "522",  # Cloudflare connection timed out
        "523",  # Cloudflare origin is unreachable
        "525",  # Cloudflare SSL handshake failed
        "connection reset",
        "connection refused",
        "timeout",
        "network unreachable",
        "read timeout",
        "connect timeout"
    ]

    # 严重错误，应该立即重连
    critical_errors = ["524", "502", "503", "504", "timeout"]

    is_recoverable = any(err in error_str for err in recoverable_errors)
    is_critical = any(err in error_str for err in critical_errors)

    if is_recoverable:
        logger.info(f"检测到可恢复错误: {error}")
        reconnect_manager.record_error(name, str(error))

        # 对于严重错误或满足重连条件的错误，尝试重连
        if is_critical or reconnect_manager.should_reconnect(name):
            logger.info(f"尝试自动重连 {name}... (严重错误: {is_critical})")
            success = await reconnect_manager.attempt_reconnect(name)
            if success:
                logger.info(f"自动重连 {name} 成功")
                return True
            else:
                logger.warning(f"自动重连 {name} 失败")

    return False
