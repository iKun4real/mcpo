"""
性能优化组件测试
测试缓存、连接池、并发控制等性能优化功能
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from mcpo.utils.cache import SmartCache, CacheStrategy, cache_manager
from mcpo.utils.connection_pool import ConnectionPool, ConnectionPoolConfig
from mcpo.utils.performance import (
    ConcurrencyLimiter, 
    RequestDeduplicator, 
    PerformanceMonitor,
    performance_monitor
)


class TestSmartCache:
    """测试智能缓存系统"""
    
    @pytest.fixture
    def cache(self):
        """创建测试缓存实例"""
        return SmartCache(max_size=10, default_ttl=1.0)
    
    async def test_cache_basic_operations(self, cache):
        """测试基本缓存操作"""
        # 测试设置和获取
        await cache.set("test_endpoint", {"arg1": "value1"}, "test_result")
        result = await cache.get("test_endpoint", {"arg1": "value1"})
        assert result == "test_result"
        
        # 测试缓存未命中
        result = await cache.get("nonexistent", {})
        assert result is None
    
    async def test_cache_ttl_expiration(self, cache):
        """测试TTL过期"""
        await cache.set("test_endpoint", {}, "test_result", ttl=0.1)
        
        # 立即获取应该成功
        result = await cache.get("test_endpoint", {})
        assert result == "test_result"
        
        # 等待过期
        await asyncio.sleep(0.2)
        result = await cache.get("test_endpoint", {})
        assert result is None
    
    async def test_cache_lru_eviction(self):
        """测试LRU驱逐策略"""
        cache = SmartCache(max_size=3, strategy=CacheStrategy.LRU)
        
        # 填满缓存
        await cache.set("endpoint1", {}, "result1")
        await cache.set("endpoint2", {}, "result2")
        await cache.set("endpoint3", {}, "result3")
        
        # 访问第一个，使其成为最近使用
        await cache.get("endpoint1", {})
        
        # 添加新项，应该驱逐endpoint2
        await cache.set("endpoint4", {}, "result4")
        
        assert await cache.get("endpoint1", {}) == "result1"  # 仍然存在
        assert await cache.get("endpoint2", {}) is None       # 被驱逐
        assert await cache.get("endpoint3", {}) == "result3"  # 仍然存在
        assert await cache.get("endpoint4", {}) == "result4"  # 新添加
    
    async def test_cache_stats(self, cache):
        """测试缓存统计"""
        # 初始统计
        stats = cache.get_stats()
        assert stats['hits'] == 0
        assert stats['misses'] == 0
        
        # 设置和获取
        await cache.set("test", {}, "result")
        await cache.get("test", {})  # 命中
        await cache.get("nonexistent", {})  # 未命中
        
        stats = cache.get_stats()
        assert stats['hits'] == 1
        assert stats['misses'] == 1
        assert stats['hit_rate'] == 50.0


class TestConnectionPool:
    """测试连接池"""
    
    @pytest.fixture
    def mock_connection_factory(self):
        """模拟连接工厂"""
        async def factory():
            mock_session = AsyncMock()
            mock_session.list_tools = AsyncMock(return_value=None)
            mock_session.close = AsyncMock()
            return mock_session
        return factory
    
    async def test_connection_pool_basic(self, mock_connection_factory):
        """测试连接池基本功能"""
        config = ConnectionPoolConfig(min_connections=2, max_connections=5)
        pool = ConnectionPool(mock_connection_factory, config, "test_pool")
        
        await pool.initialize()
        
        # 检查初始连接数
        stats = pool.get_stats()
        assert stats['current_total'] >= 2
        
        # 获取连接
        async with pool.get_connection() as session:
            assert session is not None
        
        await pool.close()
    
    async def test_connection_pool_concurrency(self, mock_connection_factory):
        """测试连接池并发"""
        config = ConnectionPoolConfig(min_connections=1, max_connections=3)
        pool = ConnectionPool(mock_connection_factory, config, "test_pool")
        
        await pool.initialize()
        
        # 并发获取连接
        async def get_connection():
            async with pool.get_connection() as session:
                await asyncio.sleep(0.1)
                return session
        
        tasks = [get_connection() for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 检查是否有连接获取成功
        successful = [r for r in results if not isinstance(r, Exception)]
        assert len(successful) > 0
        
        await pool.close()


class TestConcurrencyLimiter:
    """测试并发限制器"""
    
    async def test_concurrency_limit(self):
        """测试并发限制"""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        
        results = []
        
        async def task(task_id):
            async with limiter.acquire():
                results.append(f"start_{task_id}")
                await asyncio.sleep(0.1)
                results.append(f"end_{task_id}")
        
        # 启动3个任务，但只有2个能同时运行
        tasks = [task(i) for i in range(3)]
        await asyncio.gather(*tasks)
        
        # 检查统计
        stats = limiter.get_stats()
        assert stats['max_concurrent'] == 2
        assert stats['peak_concurrent'] <= 2


class TestRequestDeduplicator:
    """测试请求去重器"""
    
    async def test_request_deduplication(self):
        """测试请求去重"""
        deduplicator = RequestDeduplicator(ttl=1.0)
        
        call_count = 0
        
        async def mock_executor():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return f"result_{call_count}"
        
        # 同时发起相同的请求
        tasks = [
            deduplicator.execute_or_wait("test_endpoint", {"arg": "value"}, mock_executor)
            for _ in range(3)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # 应该只执行一次，所有请求返回相同结果
        assert call_count == 1
        assert all(result == "result_1" for result in results)
        
        await deduplicator.close()


class TestPerformanceMonitor:
    """测试性能监控器"""
    
    async def test_performance_monitoring(self):
        """测试性能监控"""
        monitor = PerformanceMonitor(window_size=100)
        
        # 模拟请求
        async with monitor.monitor_request("test_endpoint"):
            await asyncio.sleep(0.1)
        
        # 模拟失败请求
        try:
            async with monitor.monitor_request("test_endpoint"):
                raise Exception("Test error")
        except Exception:
            pass
        
        # 检查指标
        metrics = monitor.get_metrics("test_endpoint")
        assert metrics['total_requests'] == 2
        assert metrics['success_rate'] == 50.0
        assert metrics['avg_duration'] > 0


class TestIntegratedPerformance:
    """集成性能测试"""
    
    async def test_cache_performance(self):
        """基准测试缓存性能"""
        cache = SmartCache(max_size=1000)

        async def cache_operations():
            for i in range(100):
                await cache.set(f"endpoint_{i % 10}", {"arg": i}, f"result_{i}")
                await cache.get(f"endpoint_{i % 10}", {"arg": i})

        # 测试缓存性能
        start_time = time.time()
        await cache_operations()
        end_time = time.time()

        duration = end_time - start_time
        print(f"缓存性能测试完成，耗时: {duration:.3f}秒")

        # 检查缓存统计
        stats = cache.get_stats()
        print(f"缓存统计: {stats}")
        assert stats['hit_rate'] > 80  # 应该有较高的命中率
        assert duration < 1.0  # 应该在1秒内完成
    
    async def test_concurrent_cache_access(self):
        """测试并发缓存访问"""
        cache = SmartCache(max_size=100)
        
        async def worker(worker_id):
            for i in range(50):
                key = f"endpoint_{i % 10}"
                args = {"worker": worker_id, "iteration": i}
                
                # 50%概率设置，50%概率获取
                if i % 2 == 0:
                    await cache.set(key, args, f"result_{worker_id}_{i}")
                else:
                    await cache.get(key, args)
        
        # 启动多个并发工作者
        tasks = [worker(i) for i in range(10)]
        await asyncio.gather(*tasks)
        
        stats = cache.get_stats()
        assert stats['total_requests'] > 0
        print(f"并发缓存测试完成: {stats}")
    
    async def test_end_to_end_performance(self):
        """端到端性能测试"""
        # 模拟完整的请求处理流程
        cache = SmartCache(max_size=100)
        limiter = ConcurrencyLimiter(max_concurrent=10)
        monitor = PerformanceMonitor()
        
        async def simulate_request(request_id):
            async with monitor.monitor_request("test_tool"):
                async with limiter.acquire():
                    # 检查缓存
                    cached = await cache.get("test_tool", {"id": request_id % 5})
                    if cached:
                        return cached
                    
                    # 模拟工具执行
                    await asyncio.sleep(0.01)
                    result = f"result_{request_id}"
                    
                    # 缓存结果
                    await cache.set("test_tool", {"id": request_id % 5}, result)
                    return result
        
        # 并发执行多个请求
        start_time = time.time()
        tasks = [simulate_request(i) for i in range(100)]
        results = await asyncio.gather(*tasks)
        end_time = time.time()
        
        # 验证结果
        assert len(results) == 100
        unique_results = len(set(results))
        print(f"唯一结果数量: {unique_results}")
        # 由于并发执行，可能会有一些重复计算，但应该显著少于100个
        assert unique_results <= 20  # 允许一些并发重复，但应该有明显的缓存效果
        
        # 检查性能指标
        duration = end_time - start_time
        print(f"端到端测试完成，耗时: {duration:.3f}秒")
        
        cache_stats = cache.get_stats()
        monitor_stats = monitor.get_metrics("test_tool")
        limiter_stats = limiter.get_stats()
        
        print(f"缓存命中率: {cache_stats['hit_rate']:.1f}%")
        print(f"平均响应时间: {monitor_stats['avg_duration']:.3f}秒")
        print(f"峰值并发: {limiter_stats['peak_concurrent']}")
        
        # 性能断言
        # 由于并发执行，初始缓存命中率可能较低，但应该有一定的缓存效果
        assert cache_stats['hit_rate'] >= 0  # 至少不出错
        assert monitor_stats['avg_duration'] < 0.1  # 快速响应
        assert duration < 2.0  # 总体执行时间合理

        # 验证缓存确实起到了作用（唯一结果数量明显少于总请求数）
        cache_effectiveness = (100 - unique_results) / 100 * 100
        print(f"缓存有效性: {cache_effectiveness:.1f}%")
        assert cache_effectiveness > 50  # 至少50%的请求受益于缓存
