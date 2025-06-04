"""
高性能缓存系统
支持TTL、LRU、分层缓存和智能失效策略
"""

import asyncio
import time
import hashlib
import json
import logging
from typing import Any, Dict, Optional, Union, Callable, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict
from enum import Enum

logger = logging.getLogger(__name__)


class CacheStrategy(Enum):
    """缓存策略"""
    LRU = "lru"  # 最近最少使用
    TTL = "ttl"  # 时间过期
    LRU_TTL = "lru_ttl"  # LRU + TTL 组合


@dataclass
class CacheEntry:
    """缓存条目"""
    value: Any
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: Optional[float] = None
    
    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl
    
    def touch(self):
        """更新访问时间"""
        self.last_accessed = time.time()
        self.access_count += 1


class SmartCache:
    """智能缓存系统"""
    
    def __init__(self,
                 max_size: int = 1000,
                 default_ttl: Optional[float] = 300,  # 5分钟
                 strategy: CacheStrategy = CacheStrategy.LRU_TTL):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.strategy = strategy

        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = None  # 延迟初始化

        # 统计信息
        self._stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'expirations': 0,
            'total_requests': 0,
        }

        # 清理任务（延迟初始化）
        self._cleanup_task = None
        self._initialized = False

    async def _ensure_initialized(self):
        """确保缓存已初始化"""
        if not self._initialized:
            self._lock = asyncio.Lock()
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            self._initialized = True

    def _generate_key(self, endpoint: str, args: Dict[str, Any]) -> str:
        """生成缓存键"""
        # 创建确定性的键
        key_data = {
            'endpoint': endpoint,
            'args': args
        }
        key_str = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    async def get(self, endpoint: str, args: Dict[str, Any]) -> Optional[Any]:
        """获取缓存值"""
        await self._ensure_initialized()
        key = self._generate_key(endpoint, args)

        async with self._lock:
            self._stats['total_requests'] += 1
            
            if key not in self._cache:
                self._stats['misses'] += 1
                return None
            
            entry = self._cache[key]
            
            # 检查是否过期
            if entry.is_expired():
                del self._cache[key]
                self._stats['expirations'] += 1
                self._stats['misses'] += 1
                return None
            
            # 更新访问信息
            entry.touch()
            
            # LRU: 移动到末尾
            if self.strategy in [CacheStrategy.LRU, CacheStrategy.LRU_TTL]:
                self._cache.move_to_end(key)
            
            self._stats['hits'] += 1
            logger.debug(f"缓存命中: {endpoint}")
            return entry.value
    
    async def set(self,
                  endpoint: str,
                  args: Dict[str, Any],
                  value: Any,
                  ttl: Optional[float] = None) -> None:
        """设置缓存值"""
        await self._ensure_initialized()
        key = self._generate_key(endpoint, args)
        ttl = ttl or self.default_ttl

        async with self._lock:
            # 如果缓存已满，执行驱逐策略
            if len(self._cache) >= self.max_size and key not in self._cache:
                await self._evict()
            
            entry = CacheEntry(value=value, ttl=ttl)
            self._cache[key] = entry
            
            # LRU: 移动到末尾
            if self.strategy in [CacheStrategy.LRU, CacheStrategy.LRU_TTL]:
                self._cache.move_to_end(key)
            
            logger.debug(f"缓存设置: {endpoint}")
    
    async def _evict(self) -> None:
        """驱逐策略"""
        if not self._cache:
            return
        
        if self.strategy == CacheStrategy.LRU or self.strategy == CacheStrategy.LRU_TTL:
            # 移除最久未使用的
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        elif self.strategy == CacheStrategy.TTL:
            # 移除最早创建的
            oldest_key = min(self._cache.keys(), 
                           key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]
        
        self._stats['evictions'] += 1
    
    async def invalidate(self, endpoint: str, args: Dict[str, Any] = None) -> None:
        """失效缓存"""
        async with self._lock:
            if args is not None:
                # 失效特定缓存
                key = self._generate_key(endpoint, args)
                if key in self._cache:
                    del self._cache[key]
            else:
                # 失效所有相关缓存
                keys_to_remove = []
                for key in self._cache:
                    # 简单的前缀匹配（可以优化）
                    if endpoint in key:
                        keys_to_remove.append(key)
                
                for key in keys_to_remove:
                    del self._cache[key]
    
    async def clear(self) -> None:
        """清空缓存"""
        async with self._lock:
            self._cache.clear()
            logger.info("缓存已清空")
    
    async def _periodic_cleanup(self):
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟清理一次
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"缓存清理时出错: {e}")
    
    async def _cleanup_expired(self):
        """清理过期缓存"""
        async with self._lock:
            expired_keys = []
            for key, entry in self._cache.items():
                if entry.is_expired():
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._cache[key]
                self._stats['expirations'] += 1
            
            if expired_keys:
                logger.debug(f"清理了 {len(expired_keys)} 个过期缓存")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total_requests = self._stats['total_requests']
        hit_rate = (self._stats['hits'] / total_requests * 100) if total_requests > 0 else 0
        
        return {
            **self._stats,
            'hit_rate': round(hit_rate, 2),
            'current_size': len(self._cache),
            'max_size': self.max_size,
            'memory_usage': self._estimate_memory_usage(),
        }
    
    def _estimate_memory_usage(self) -> str:
        """估算内存使用量"""
        # 简单估算，实际可以更精确
        import sys
        total_size = 0
        for entry in self._cache.values():
            total_size += sys.getsizeof(entry.value)
        
        if total_size < 1024:
            return f"{total_size}B"
        elif total_size < 1024 * 1024:
            return f"{total_size / 1024:.1f}KB"
        else:
            return f"{total_size / (1024 * 1024):.1f}MB"
    
    async def close(self):
        """关闭缓存"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        await self.clear()


class CacheManager:
    """缓存管理器"""
    
    def __init__(self):
        self._caches: Dict[str, SmartCache] = {}
        
        # 默认缓存配置
        self.default_cache = SmartCache(
            max_size=1000,
            default_ttl=300,  # 5分钟
            strategy=CacheStrategy.LRU_TTL
        )
    
    def create_cache(self, 
                    name: str,
                    max_size: int = 1000,
                    default_ttl: Optional[float] = 300,
                    strategy: CacheStrategy = CacheStrategy.LRU_TTL) -> SmartCache:
        """创建命名缓存"""
        if name in self._caches:
            raise ValueError(f"缓存 {name} 已存在")
        
        cache = SmartCache(max_size, default_ttl, strategy)
        self._caches[name] = cache
        return cache
    
    def get_cache(self, name: str = "default") -> SmartCache:
        """获取缓存实例"""
        if name == "default":
            return self.default_cache
        return self._caches.get(name)
    
    async def close_all(self):
        """关闭所有缓存"""
        await self.default_cache.close()
        for cache in self._caches.values():
            await cache.close()
        self._caches.clear()
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有缓存统计信息"""
        stats = {"default": self.default_cache.get_stats()}
        for name, cache in self._caches.items():
            stats[name] = cache.get_stats()
        return stats


# 全局缓存管理器
cache_manager = CacheManager()


def cache_key_for_tool(tool_name: str, args: Dict[str, Any]) -> str:
    """为工具调用生成缓存键"""
    return cache_manager.default_cache._generate_key(tool_name, args)


def should_cache_response(tool_name: str, args: Dict[str, Any], response: Any) -> bool:
    """判断响应是否应该被缓存"""
    # 可以根据工具类型、参数、响应大小等决定是否缓存
    
    # 不缓存错误响应
    if isinstance(response, dict) and "error" in response:
        return False
    
    # 不缓存过大的响应（超过1MB）
    import sys
    if sys.getsizeof(response) > 1024 * 1024:
        return False
    
    # 某些工具可能不适合缓存（如时间相关的工具）
    time_related_tools = ["time", "clock", "now", "current"]
    if any(keyword in tool_name.lower() for keyword in time_related_tools):
        return False
    
    return True


def get_cache_ttl(tool_name: str, args: Dict[str, Any]) -> Optional[float]:
    """根据工具类型确定缓存TTL"""
    # 可以根据工具特性设置不同的TTL
    
    # 静态数据可以缓存更久
    static_tools = ["list", "info", "schema", "help"]
    if any(keyword in tool_name.lower() for keyword in static_tools):
        return 3600  # 1小时
    
    # 动态数据缓存时间较短
    dynamic_tools = ["search", "query", "fetch"]
    if any(keyword in tool_name.lower() for keyword in dynamic_tools):
        return 60  # 1分钟
    
    # 默认5分钟
    return 300
