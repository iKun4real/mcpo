"""
性能优化工具集
包括并发控制、请求去重、批处理和性能监控
"""

import asyncio
import time
import logging
from typing import Any, Dict, List, Optional, Callable, Tuple, Set
from dataclasses import dataclass, field
from collections import defaultdict, deque
from contextlib import asynccontextmanager
import weakref

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """性能指标"""
    request_count: int = 0
    total_duration: float = 0.0
    min_duration: float = float('inf')
    max_duration: float = 0.0
    error_count: int = 0
    concurrent_requests: int = 0
    peak_concurrent: int = 0
    
    def add_request(self, duration: float, success: bool = True):
        """添加请求记录"""
        self.request_count += 1
        self.total_duration += duration
        self.min_duration = min(self.min_duration, duration)
        self.max_duration = max(self.max_duration, duration)
        if not success:
            self.error_count += 1
    
    def get_avg_duration(self) -> float:
        """获取平均响应时间"""
        return self.total_duration / self.request_count if self.request_count > 0 else 0.0
    
    def get_success_rate(self) -> float:
        """获取成功率"""
        return (self.request_count - self.error_count) / self.request_count * 100 if self.request_count > 0 else 0.0


class ConcurrencyLimiter:
    """并发限制器"""
    
    def __init__(self, max_concurrent: int = 100):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._current_count = 0
        self._peak_count = 0
        self._lock = asyncio.Lock()
    
    @asynccontextmanager
    async def acquire(self):
        """获取并发许可"""
        async with self._semaphore:
            async with self._lock:
                self._current_count += 1
                self._peak_count = max(self._peak_count, self._current_count)
            
            try:
                yield
            finally:
                async with self._lock:
                    self._current_count -= 1
    
    def get_stats(self) -> Dict[str, int]:
        """获取并发统计"""
        return {
            'max_concurrent': self.max_concurrent,
            'current_concurrent': self._current_count,
            'peak_concurrent': self._peak_count,
        }


class RequestDeduplicator:
    """请求去重器"""
    
    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._lock = None  # 延迟初始化

        # 清理任务（延迟初始化）
        self._cleanup_task = None
        self._initialized = False

    async def _ensure_initialized(self):
        """确保去重器已初始化"""
        if not self._initialized:
            self._lock = asyncio.Lock()
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            self._initialized = True
    
    def _generate_key(self, endpoint: str, args: Dict[str, Any]) -> str:
        """生成请求键"""
        import hashlib
        import json
        key_data = {'endpoint': endpoint, 'args': args}
        key_str = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    async def execute_or_wait(self,
                             endpoint: str,
                             args: Dict[str, Any],
                             executor: Callable) -> Any:
        """执行请求或等待重复请求完成"""
        await self._ensure_initialized()
        key = self._generate_key(endpoint, args)

        # 检查是否有待处理的请求
        async with self._lock:
            if key in self._pending_requests:
                # 获取已有的future
                existing_future = self._pending_requests[key]
                logger.debug(f"等待重复请求完成: {endpoint}")

        # 如果有已存在的请求，等待它完成
        if 'existing_future' in locals():
            try:
                return await existing_future
            except Exception as e:
                # 如果等待的请求失败，继续创建新请求
                logger.debug(f"等待的请求失败，重新执行: {endpoint}")

        # 创建新请求
        async with self._lock:
            # 再次检查，防止竞态条件
            if key in self._pending_requests:
                return await self._pending_requests[key]

            # 创建新的任务
            future = asyncio.create_task(self._execute_with_cleanup(key, executor))
            self._pending_requests[key] = future

        # 在锁外等待任务完成
        return await future
    
    async def _execute_with_cleanup(self, key: str, executor: Callable) -> Any:
        """执行请求并清理"""
        try:
            result = await executor()
            return result
        finally:
            async with self._lock:
                if key in self._pending_requests:
                    del self._pending_requests[key]
    
    async def _periodic_cleanup(self):
        """定期清理过期请求"""
        while True:
            try:
                await asyncio.sleep(self.ttl)
                async with self._lock:
                    # 清理已完成的请求
                    completed_keys = [
                        key for key, future in self._pending_requests.items()
                        if future.done()
                    ]
                    for key in completed_keys:
                        del self._pending_requests[key]
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"请求去重清理时出错: {e}")
    
    async def close(self):
        """关闭去重器"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        # 取消所有待处理的请求
        async with self._lock:
            for future in self._pending_requests.values():
                if not future.done():
                    future.cancel()
            self._pending_requests.clear()


class BatchProcessor:
    """批处理器"""
    
    def __init__(self, 
                 batch_size: int = 10,
                 batch_timeout: float = 0.1,
                 max_wait_time: float = 1.0):
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.max_wait_time = max_wait_time
        
        self._batches: Dict[str, List[Tuple[Dict[str, Any], asyncio.Future]]] = defaultdict(list)
        self._batch_timers: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
    
    async def add_request(self, 
                         endpoint: str, 
                         args: Dict[str, Any],
                         executor: Callable) -> Any:
        """添加请求到批处理队列"""
        future = asyncio.Future()
        
        async with self._lock:
            self._batches[endpoint].append((args, future))
            
            # 如果达到批处理大小，立即处理
            if len(self._batches[endpoint]) >= self.batch_size:
                await self._process_batch(endpoint, executor)
            else:
                # 设置定时器
                if endpoint not in self._batch_timers:
                    self._batch_timers[endpoint] = asyncio.create_task(
                        self._batch_timer(endpoint, executor)
                    )
        
        return await future
    
    async def _batch_timer(self, endpoint: str, executor: Callable):
        """批处理定时器"""
        try:
            await asyncio.sleep(self.batch_timeout)
            async with self._lock:
                if endpoint in self._batches and self._batches[endpoint]:
                    await self._process_batch(endpoint, executor)
        except asyncio.CancelledError:
            pass
    
    async def _process_batch(self, endpoint: str, executor: Callable):
        """处理批次"""
        if endpoint not in self._batches or not self._batches[endpoint]:
            return
        
        batch = self._batches[endpoint]
        self._batches[endpoint] = []
        
        # 取消定时器
        if endpoint in self._batch_timers:
            self._batch_timers[endpoint].cancel()
            del self._batch_timers[endpoint]
        
        logger.debug(f"处理批次: {endpoint}, 大小: {len(batch)}")
        
        # 并发执行批次中的所有请求
        tasks = []
        for args, future in batch:
            task = asyncio.create_task(self._execute_single(executor, args, future))
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _execute_single(self, executor: Callable, args: Dict[str, Any], future: asyncio.Future):
        """执行单个请求"""
        try:
            result = await executor(args)
            if not future.done():
                future.set_result(result)
        except Exception as e:
            if not future.done():
                future.set_exception(e)


class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self._metrics: Dict[str, PerformanceMetrics] = defaultdict(PerformanceMetrics)
        self._recent_requests: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._lock = asyncio.Lock()
    
    @asynccontextmanager
    async def monitor_request(self, endpoint: str):
        """监控请求性能"""
        start_time = time.time()
        success = True
        
        async with self._lock:
            self._metrics[endpoint].concurrent_requests += 1
            self._metrics[endpoint].peak_concurrent = max(
                self._metrics[endpoint].peak_concurrent,
                self._metrics[endpoint].concurrent_requests
            )
        
        try:
            yield
        except Exception:
            success = False
            raise
        finally:
            end_time = time.time()
            duration = end_time - start_time
            
            async with self._lock:
                self._metrics[endpoint].concurrent_requests -= 1
                self._metrics[endpoint].add_request(duration, success)
                self._recent_requests[endpoint].append({
                    'timestamp': end_time,
                    'duration': duration,
                    'success': success
                })
    
    def get_metrics(self, endpoint: str = None) -> Dict[str, Any]:
        """获取性能指标"""
        if endpoint:
            metrics = self._metrics[endpoint]
            recent = list(self._recent_requests[endpoint])
            
            # 计算最近的QPS
            now = time.time()
            recent_1s = [r for r in recent if now - r['timestamp'] <= 1.0]
            recent_5s = [r for r in recent if now - r['timestamp'] <= 5.0]
            
            return {
                'endpoint': endpoint,
                'total_requests': metrics.request_count,
                'avg_duration': round(metrics.get_avg_duration(), 3),
                'min_duration': round(metrics.min_duration, 3) if metrics.min_duration != float('inf') else 0,
                'max_duration': round(metrics.max_duration, 3),
                'success_rate': round(metrics.get_success_rate(), 2),
                'current_concurrent': metrics.concurrent_requests,
                'peak_concurrent': metrics.peak_concurrent,
                'qps_1s': len(recent_1s),
                'qps_5s': len(recent_5s) / 5.0,
            }
        else:
            return {endpoint: self.get_metrics(endpoint) for endpoint in self._metrics.keys()}


# 全局性能组件
concurrency_limiter = ConcurrencyLimiter(max_concurrent=100)
request_deduplicator = RequestDeduplicator(ttl=60.0)
performance_monitor = PerformanceMonitor(window_size=1000)


async def close_performance_components():
    """关闭性能组件"""
    await request_deduplicator.close()
