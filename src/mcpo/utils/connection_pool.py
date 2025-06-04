"""
高性能连接池管理器
提供连接复用、负载均衡和自动扩缩容功能
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from mcp import ClientSession

logger = logging.getLogger(__name__)


@dataclass
class ConnectionPoolConfig:
    """连接池配置"""
    min_connections: int = 2  # 最小连接数
    max_connections: int = 10  # 最大连接数
    max_idle_time: int = 300  # 最大空闲时间（秒）
    connection_timeout: float = 30.0  # 连接超时
    health_check_interval: int = 60  # 健康检查间隔（秒）
    retry_attempts: int = 3  # 重试次数
    retry_delay: float = 1.0  # 重试延迟


@dataclass
class PooledConnection:
    """池化连接对象"""
    session: ClientSession
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: bool = False
    error_count: int = 0
    is_healthy: bool = True


class ConnectionPool:
    """高性能连接池"""
    
    def __init__(self, 
                 connection_factory: Callable,
                 config: ConnectionPoolConfig = None,
                 name: str = "default"):
        self.connection_factory = connection_factory
        self.config = config or ConnectionPoolConfig()
        self.name = name
        
        self._connections: List[PooledConnection] = []
        self._lock = asyncio.Lock()
        self._stats = {
            'total_created': 0,
            'total_destroyed': 0,
            'current_active': 0,
            'peak_active': 0,
            'total_requests': 0,
            'failed_requests': 0,
        }
        
        # 启动后台任务
        self._cleanup_task = None
        self._health_check_task = None
        
    async def initialize(self):
        """初始化连接池"""
        logger.info(f"初始化连接池 {self.name}，最小连接数: {self.config.min_connections}")
        
        # 创建最小连接数
        for _ in range(self.config.min_connections):
            try:
                await self._create_connection()
            except Exception as e:
                logger.warning(f"初始化连接失败: {e}")
        
        # 启动后台任务
        self._cleanup_task = asyncio.create_task(self._cleanup_idle_connections())
        self._health_check_task = asyncio.create_task(self._periodic_health_check())
        
        logger.info(f"连接池 {self.name} 初始化完成，当前连接数: {len(self._connections)}")
    
    async def _create_connection(self) -> PooledConnection:
        """创建新连接"""
        try:
            session = await self.connection_factory()
            connection = PooledConnection(session=session)
            self._connections.append(connection)
            self._stats['total_created'] += 1
            logger.debug(f"创建新连接，当前总数: {len(self._connections)}")
            return connection
        except Exception as e:
            logger.error(f"创建连接失败: {e}")
            raise
    
    @asynccontextmanager
    async def get_connection(self):
        """获取连接（上下文管理器）"""
        connection = None
        try:
            connection = await self._acquire_connection()
            self._stats['total_requests'] += 1
            yield connection.session
        except Exception as e:
            self._stats['failed_requests'] += 1
            if connection:
                connection.error_count += 1
                connection.is_healthy = False
            raise
        finally:
            if connection:
                await self._release_connection(connection)
    
    async def _acquire_connection(self) -> PooledConnection:
        """获取可用连接"""
        async with self._lock:
            # 查找可用的健康连接
            for conn in self._connections:
                if not conn.in_use and conn.is_healthy:
                    conn.in_use = True
                    conn.last_used = time.time()
                    self._stats['current_active'] += 1
                    self._stats['peak_active'] = max(
                        self._stats['peak_active'], 
                        self._stats['current_active']
                    )
                    return conn
            
            # 如果没有可用连接且未达到最大连接数，创建新连接
            if len(self._connections) < self.config.max_connections:
                conn = await self._create_connection()
                conn.in_use = True
                conn.last_used = time.time()
                self._stats['current_active'] += 1
                self._stats['peak_active'] = max(
                    self._stats['peak_active'], 
                    self._stats['current_active']
                )
                return conn
            
            # 等待连接可用（简单实现，可以优化为队列）
            raise Exception("连接池已满，无可用连接")
    
    async def _release_connection(self, connection: PooledConnection):
        """释放连接"""
        async with self._lock:
            connection.in_use = False
            connection.last_used = time.time()
            self._stats['current_active'] -= 1
    
    async def _cleanup_idle_connections(self):
        """清理空闲连接"""
        while True:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                current_time = time.time()
                
                async with self._lock:
                    connections_to_remove = []
                    
                    for conn in self._connections:
                        if (not conn.in_use and 
                            current_time - conn.last_used > self.config.max_idle_time and
                            len(self._connections) > self.config.min_connections):
                            connections_to_remove.append(conn)
                    
                    for conn in connections_to_remove:
                        try:
                            await conn.session.close()
                        except:
                            pass
                        self._connections.remove(conn)
                        self._stats['total_destroyed'] += 1
                        logger.debug(f"清理空闲连接，剩余连接数: {len(self._connections)}")
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理空闲连接时出错: {e}")
    
    async def _periodic_health_check(self):
        """定期健康检查"""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await self._check_all_connections_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检查时出错: {e}")
    
    async def _check_all_connections_health(self):
        """检查所有连接健康状态"""
        async with self._lock:
            unhealthy_connections = []
            
            for conn in self._connections:
                if not conn.in_use:  # 只检查空闲连接
                    try:
                        # 简单的健康检查
                        await asyncio.wait_for(
                            conn.session.list_tools(), 
                            timeout=5.0
                        )
                        conn.is_healthy = True
                        conn.error_count = 0
                    except Exception:
                        conn.is_healthy = False
                        conn.error_count += 1
                        if conn.error_count >= 3:
                            unhealthy_connections.append(conn)
            
            # 移除不健康的连接
            for conn in unhealthy_connections:
                try:
                    await conn.session.close()
                except:
                    pass
                self._connections.remove(conn)
                self._stats['total_destroyed'] += 1
                logger.warning(f"移除不健康连接，剩余连接数: {len(self._connections)}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取连接池统计信息"""
        return {
            **self._stats,
            'current_total': len(self._connections),
            'current_idle': len([c for c in self._connections if not c.in_use]),
            'current_healthy': len([c for c in self._connections if c.is_healthy]),
        }
    
    async def close(self):
        """关闭连接池"""
        logger.info(f"关闭连接池 {self.name}")
        
        # 取消后台任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._health_check_task:
            self._health_check_task.cancel()
        
        # 关闭所有连接
        async with self._lock:
            for conn in self._connections:
                try:
                    await conn.session.close()
                except:
                    pass
            self._connections.clear()


class ConnectionPoolManager:
    """连接池管理器"""
    
    def __init__(self):
        self._pools: Dict[str, ConnectionPool] = {}
    
    async def create_pool(self, 
                         name: str, 
                         connection_factory: Callable,
                         config: ConnectionPoolConfig = None) -> ConnectionPool:
        """创建连接池"""
        if name in self._pools:
            raise ValueError(f"连接池 {name} 已存在")
        
        pool = ConnectionPool(connection_factory, config, name)
        await pool.initialize()
        self._pools[name] = pool
        return pool
    
    def get_pool(self, name: str) -> Optional[ConnectionPool]:
        """获取连接池"""
        return self._pools.get(name)
    
    async def close_pool(self, name: str):
        """关闭指定连接池"""
        if name in self._pools:
            await self._pools[name].close()
            del self._pools[name]
    
    async def close_all(self):
        """关闭所有连接池"""
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有连接池统计信息"""
        return {name: pool.get_stats() for name, pool in self._pools.items()}


# 全局连接池管理器
pool_manager = ConnectionPoolManager()
