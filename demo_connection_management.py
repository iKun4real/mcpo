#!/usr/bin/env python3
"""
MCPO 连接管理功能演示脚本

这个脚本演示了新的连接管理功能，包括：
1. 连接重试机制
2. 连接超时控制
3. 健康监控
4. 改进的错误处理
"""

import asyncio
import logging
from unittest.mock import AsyncMock
from src.mcpo.main import (
    retry_connection,
    create_connection_with_timeout,
    ConnectionManager,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_DELAY,
    DEFAULT_CONNECTION_TIMEOUT
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def demo_retry_mechanism():
    """演示连接重试机制"""
    print("\n=== 演示连接重试机制 ===")
    
    # 模拟一个不稳定的连接
    attempt_count = 0
    
    async def unstable_connection():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise Exception(f"连接失败 (第 {attempt_count} 次尝试)")
        return f"连接成功！(总共尝试了 {attempt_count} 次)"
    
    try:
        result = await retry_connection(
            unstable_connection,
            max_attempts=5,
            delay=0.5,  # 减少演示时间
            connection_name="演示服务器"
        )
        print(f"结果: {result}")
    except Exception as e:
        print(f"连接最终失败: {e}")


async def demo_timeout_mechanism():
    """演示连接超时机制"""
    print("\n=== 演示连接超时机制 ===")
    
    # 模拟快速连接
    async def fast_connection():
        await asyncio.sleep(0.1)
        return "快速连接成功"
    
    # 模拟慢速连接
    async def slow_connection():
        await asyncio.sleep(2.0)
        return "慢速连接成功"
    
    # 测试快速连接
    try:
        result = await create_connection_with_timeout(fast_connection, timeout=1.0)
        print(f"快速连接结果: {result}")
    except Exception as e:
        print(f"快速连接失败: {e}")
    
    # 测试慢速连接
    try:
        result = await create_connection_with_timeout(slow_connection, timeout=1.0)
        print(f"慢速连接结果: {result}")
    except Exception as e:
        print(f"慢速连接失败: {e}")


async def demo_connection_manager():
    """演示优化后的连接管理器"""
    print("\n=== 演示优化后的连接管理器 ===")

    manager = ConnectionManager()

    # 创建模拟会话
    mock_session = AsyncMock()
    mock_session.list_tools = AsyncMock(return_value=None)

    # 注册连接
    manager.register_connection("demo_server", mock_session)
    print(f"已注册连接: {list(manager.connections.keys())}")

    # 获取初始状态
    status = manager.get_connection_status("demo_server")
    print(f"初始状态: {status['status']}")

    # 模拟连接错误
    manager.record_connection_error("demo_server", "模拟连接失败")
    status = manager.get_connection_status("demo_server")
    print(f"错误后状态: {status['status']}, 错误次数: {status['error_count']}")

    # 模拟连接恢复
    manager.record_connection_success("demo_server")
    status = manager.get_connection_status("demo_server")
    print(f"恢复后状态: {status['status']}, 错误次数: {status['error_count']}")

    # 按需健康检查
    is_healthy = await manager.check_connection_health("demo_server", mock_session)
    print(f"按需健康检查结果: {'健康' if is_healthy else '不健康'}")

    # 注销连接
    manager.unregister_connection("demo_server")
    print(f"剩余连接: {list(manager.connections.keys())}")


async def demo_error_handling():
    """演示错误处理"""
    print("\n=== 演示错误处理 ===")
    
    # 模拟不同类型的错误
    async def connection_error():
        raise ConnectionError("网络连接中断")
    
    async def timeout_error():
        raise TimeoutError("连接超时")
    
    async def general_error():
        raise Exception("未知错误")
    
    # 测试不同错误的处理
    for error_func, error_name in [
        (connection_error, "连接错误"),
        (timeout_error, "超时错误"),
        (general_error, "一般错误")
    ]:
        try:
            await retry_connection(
                error_func,
                max_attempts=2,
                delay=0.1,
                connection_name=f"测试{error_name}"
            )
        except Exception as e:
            print(f"{error_name}: {type(e).__name__} - {e}")


def demo_configuration():
    """演示配置参数"""
    print("\n=== 演示配置参数 ===")
    
    print(f"默认重试次数: {DEFAULT_RETRY_ATTEMPTS}")
    print(f"默认重试延迟: {DEFAULT_RETRY_DELAY} 秒")
    print(f"默认连接超时: {DEFAULT_CONNECTION_TIMEOUT} 秒")
    
    print("\n自定义配置示例:")
    print("retry_connection(")
    print("    connection_func,")
    print("    max_attempts=5,")
    print("    delay=3.0,")
    print("    connection_name='自定义服务器'")
    print(")")


async def main():
    """主演示函数"""
    print("MCPO 连接管理功能演示")
    print("=" * 50)
    
    # 演示各个功能
    await demo_retry_mechanism()
    await demo_timeout_mechanism()
    await demo_connection_manager()
    await demo_error_handling()
    demo_configuration()
    
    print("\n演示完成！")
    print("\n🚀 优化后的主要改进:")
    print("1. ✅ 自动连接重试机制")
    print("2. ✅ 连接超时控制")
    print("3. ✅ 智能被动检测（移除定期健康检查）")
    print("4. ✅ 改进的错误处理和状态跟踪")
    print("5. ✅ 详细的日志记录")
    print("6. ✅ 按需健康检查端点")
    print("7. ✅ 连接状态缓存和错误计数")

    print("\n💡 优化效果:")
    print("- 🔥 减少不必要的网络请求（移除1分钟定期检查）")
    print("- ⚡ 更快的错误检测（实时检测而非等待检查间隔）")
    print("- 💰 更低的资源消耗（只在需要时检查连接）")
    print("- 🎯 更准确的状态跟踪（基于实际API调用结果）")

    print("\n这些优化使 MCPO 更加高效和可靠！")


if __name__ == "__main__":
    asyncio.run(main())
