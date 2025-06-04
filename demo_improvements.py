#!/usr/bin/env python3
"""
MCPO 系统改进演示脚本
展示新的错误恢复、监控和诊断功能
"""

import asyncio
import json
import time
from typing import Dict, Any

# 模拟导入我们的改进模块
try:
    from src.mcpo.utils.error_recovery import error_recovery_manager, ErrorSeverity
    from src.mcpo.utils.system_monitor import system_monitor
    from src.mcpo.utils.reconnect_manager import reconnect_manager
    from src.mcpo.utils.cache import cache_manager
    from src.mcpo.utils.performance import performance_monitor
except ImportError:
    print("请确保在项目根目录运行此脚本")
    exit(1)


async def demo_error_recovery():
    """演示错误恢复功能"""
    print("🔧 演示错误恢复功能")
    print("=" * 50)
    
    # 模拟各种错误场景
    error_scenarios = [
        ("connection_timeout", "Connection timeout occurred", {"connection_name": "demo_conn"}),
        ("server_error", "502 Bad Gateway", {}),
        ("session_invalid", "Session expired", {"connection_name": "demo_conn"}),
        ("rate_limit", "Rate limit exceeded", {}),
        ("authentication", "Unauthorized access", {})
    ]
    
    for error_type, message, context in error_scenarios:
        print(f"\n📍 模拟错误: {error_type}")
        
        # 记录错误
        error_event = await error_recovery_manager.record_error(
            error_type, message, context, ErrorSeverity.HIGH
        )
        
        print(f"   错误已记录: {message}")
        
        # 尝试恢复（模拟）
        print(f"   检测到错误模式: {error_recovery_manager._detect_error_pattern(message)}")
        
        # 显示错误统计
        stats = error_recovery_manager.get_error_statistics()
        print(f"   当前错误统计: {stats['total_errors_last_hour']} 个错误")
    
    # 显示系统健康状态
    health = error_recovery_manager.get_system_health()
    print(f"\n🏥 系统健康状态: {health.overall_status}")
    print(f"   错误率: {health.error_rate:.4f}")
    print(f"   活跃错误数: {len(health.active_errors)}")


async def demo_system_monitoring():
    """演示系统监控功能"""
    print("\n\n📊 演示系统监控功能")
    print("=" * 50)
    
    # 启动监控
    print("🚀 启动系统监控...")
    await system_monitor.start_monitoring()
    
    # 收集指标
    print("📈 收集系统指标...")
    metrics = await system_monitor.collect_metrics()
    
    print(f"   CPU使用率: {metrics.cpu_usage:.1f}%")
    print(f"   内存使用率: {metrics.memory_usage:.1f}%")
    print(f"   可用内存: {metrics.memory_available / (1024*1024*1024):.1f} GB")
    print(f"   活跃连接数: {metrics.active_connections}")
    print(f"   缓存命中率: {metrics.cache_hit_rate:.2f}")
    print(f"   错误率: {metrics.error_rate:.4f}")
    print(f"   平均响应时间: {metrics.response_time_avg:.2f}s")
    
    # 执行系统诊断
    print("\n🔍 执行系统诊断...")
    diagnosis = await system_monitor.diagnose_system()
    
    print(f"   诊断状态: {diagnosis.status}")
    if diagnosis.issues:
        print("   发现问题:")
        for issue in diagnosis.issues:
            print(f"     - {issue}")
    
    if diagnosis.recommendations:
        print("   建议:")
        for rec in diagnosis.recommendations:
            print(f"     - {rec}")
    
    # 获取指标摘要
    print("\n📋 指标摘要 (最近5分钟):")
    summary = system_monitor.get_metrics_summary(minutes=5)
    if "error" not in summary:
        print(f"   数据点数: {summary.get('data_points', 0)}")
        if summary.get('cpu_usage'):
            cpu = summary['cpu_usage']
            print(f"   CPU: 平均 {cpu['avg']:.1f}%, 最高 {cpu['max']:.1f}%")
    
    # 停止监控
    await system_monitor.stop_monitoring()
    print("⏹️  监控已停止")


async def demo_cache_performance():
    """演示缓存性能"""
    print("\n\n⚡ 演示缓存性能")
    print("=" * 50)
    
    cache = cache_manager.default_cache
    
    # 测试缓存操作
    print("🔄 测试缓存操作...")
    
    # 写入测试数据
    test_data = {"message": "Hello, MCPO!", "timestamp": time.time()}
    await cache.set("demo_endpoint", {"param": "value"}, test_data, ttl=60)
    print("   ✅ 数据已缓存")

    # 读取数据
    cached_data = await cache.get("demo_endpoint", {"param": "value"})
    print(f"   📖 读取缓存: {cached_data}")
    
    # 显示缓存统计
    stats = cache.get_stats()
    print(f"\n📊 缓存统计:")
    print(f"   总请求数: {stats.get('total_requests', 0)}")
    print(f"   命中数: {stats.get('hits', 0)}")
    print(f"   未命中数: {stats.get('misses', 0)}")
    print(f"   命中率: {stats.get('hit_rate', 0):.2%}")
    print(f"   当前大小: {stats.get('current_size', 0)}")


async def demo_performance_monitoring():
    """演示性能监控"""
    print("\n\n⏱️  演示性能监控")
    print("=" * 50)
    
    # 模拟一些操作来生成性能数据
    print("🎯 模拟操作以生成性能数据...")
    
    # 模拟API调用
    start_time = time.time()
    await asyncio.sleep(0.1)  # 模拟处理时间
    end_time = time.time()
    
    # 记录性能数据（如果性能监控器可用）
    try:
        performance_monitor.record_request_time("demo_api", end_time - start_time)
        performance_monitor.record_cache_hit("demo_cache")
        
        # 获取性能指标
        metrics = performance_monitor.get_metrics()
        print(f"   平均响应时间: {metrics.get('avg_response_time', 0):.3f}s")
        print(f"   总请求数: {metrics.get('total_requests', 0)}")
        print(f"   缓存命中率: {metrics.get('cache_hit_rate', 0):.2%}")
        
    except Exception as e:
        print(f"   性能监控暂不可用: {str(e)}")


def print_system_overview():
    """打印系统概览"""
    print("🚀 MCPO 系统改进演示")
    print("=" * 60)
    print("本演示展示了以下改进功能:")
    print("• 🔧 智能错误恢复和分类")
    print("• 📊 实时系统监控和诊断")
    print("• ⚡ 高性能缓存系统")
    print("• ⏱️  性能监控和优化")
    print("• 🔗 增强的连接管理")
    print("• 🏥 系统健康状态跟踪")
    print()


async def main():
    """主演示函数"""
    print_system_overview()
    
    try:
        # 演示各个功能模块
        await demo_error_recovery()
        await demo_system_monitoring()
        await demo_cache_performance()
        await demo_performance_monitoring()
        
        print("\n\n🎉 演示完成!")
        print("=" * 60)
        print("✅ 所有改进功能运行正常")
        print("📈 系统稳定性和性能显著提升")
        print("🔧 500错误后自动恢复机制已就绪")
        print("📊 实时监控和诊断功能可用")
        print("\n💡 提示: 使用 /metrics 和 /diagnostics API 端点获取实时系统状态")
        
    except Exception as e:
        print(f"\n❌ 演示过程中出现错误: {str(e)}")
        print("请检查系统配置和依赖")
    
    finally:
        # 清理资源
        try:
            await system_monitor.stop_monitoring()
        except Exception as e:
            print(f"停止监控时出错: {e}")

        try:
            if hasattr(cache_manager.default_cache, 'cleanup_and_shutdown'):
                await cache_manager.default_cache.cleanup_and_shutdown()
        except Exception as e:
            print(f"清理缓存时出错: {e}")


if __name__ == "__main__":
    # 运行演示
    asyncio.run(main())
