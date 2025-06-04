"""
测试错误恢复和监控系统
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch

from mcpo.utils.error_recovery import (
    ErrorRecoveryManager, 
    ErrorEvent, 
    ErrorSeverity, 
    RecoveryAction
)
from mcpo.utils.system_monitor import SystemMonitor, SystemMetrics


class TestErrorRecoveryManager:
    """测试错误恢复管理器"""

    @pytest.fixture
    def error_manager(self):
        return ErrorRecoveryManager(max_error_history=100)

    @pytest.mark.asyncio
    async def test_record_error(self, error_manager):
        """测试错误记录"""
        error_event = await error_manager.record_error(
            "test_error",
            "Test error message",
            {"connection_name": "test_conn"},
            ErrorSeverity.HIGH
        )
        
        assert error_event.error_type == "test_error"
        assert error_event.error_message == "Test error message"
        assert error_event.severity == ErrorSeverity.HIGH
        assert error_event.context["connection_name"] == "test_conn"
        assert len(error_manager.error_history) == 1

    @pytest.mark.asyncio
    async def test_error_pattern_detection(self, error_manager):
        """测试错误模式检测"""
        # 连接超时错误
        pattern = error_manager._detect_error_pattern("Connection timeout occurred")
        assert pattern == "connection_timeout"
        
        # 服务器错误
        pattern = error_manager._detect_error_pattern("500 Internal Server Error")
        assert pattern == "server_error"
        
        # 会话无效错误
        pattern = error_manager._detect_error_pattern("Session expired")
        assert pattern == "session_invalid"
        
        # 未知错误
        pattern = error_manager._detect_error_pattern("Unknown error")
        assert pattern is None

    @pytest.mark.asyncio
    async def test_connection_timeout_recovery(self, error_manager):
        """测试连接超时恢复"""
        with patch('mcpo.utils.reconnect_manager.reconnect_manager') as mock_reconnect:
            mock_reconnect.attempt_reconnect = AsyncMock(return_value=True)

            error_event = ErrorEvent(
                timestamp=time.time(),
                error_type="connection_timeout",
                error_message="Connection timeout",
                severity=ErrorSeverity.MEDIUM,
                context={"connection_name": "test_conn"}
            )

            success = await error_manager.attempt_recovery(error_event)

            assert success is True
            assert error_event.recovery_attempted is True
            assert error_event.recovery_successful is True
            assert error_event.recovery_action == RecoveryAction.RECONNECT
            mock_reconnect.attempt_reconnect.assert_called_once_with("test_conn")

    @pytest.mark.asyncio
    async def test_server_error_recovery(self, error_manager):
        """测试服务器错误恢复"""
        error_event = ErrorEvent(
            timestamp=time.time(),
            error_type="server_error",
            error_message="502 Bad Gateway",
            severity=ErrorSeverity.HIGH,
            context={}
        )
        
        start_time = time.time()
        success = await error_manager.attempt_recovery(error_event)
        end_time = time.time()
        
        assert success is True
        assert error_event.recovery_attempted is True
        assert error_event.recovery_successful is True
        assert error_event.recovery_action == RecoveryAction.RETRY
        # 应该等待了大约2秒
        assert end_time - start_time >= 2.0

    @pytest.mark.asyncio
    async def test_error_statistics(self, error_manager):
        """测试错误统计"""
        # 记录一些错误
        await error_manager.record_error("type1", "Error 1", severity=ErrorSeverity.LOW)
        await error_manager.record_error("type1", "Error 2", severity=ErrorSeverity.MEDIUM)
        await error_manager.record_error("type2", "Error 3", severity=ErrorSeverity.HIGH)
        
        # 模拟恢复尝试
        error_manager.error_history[0].recovery_attempted = True
        error_manager.error_history[0].recovery_successful = True
        error_manager.error_history[1].recovery_attempted = True
        error_manager.error_history[1].recovery_successful = False
        
        stats = error_manager.get_error_statistics()
        
        assert stats["total_errors_last_hour"] == 3
        assert stats["error_types"]["type1"] == 2
        assert stats["error_types"]["type2"] == 1
        assert stats["total_recovery_attempts"] == 2
        assert stats["recovery_success_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_system_health_update(self, error_manager):
        """测试系统健康状态更新"""
        # 记录多个错误以提高错误率
        for i in range(50):  # 增加错误数量以确保超过阈值
            await error_manager.record_error(
                f"error_{i}",
                f"Error message {i}",
                severity=ErrorSeverity.HIGH
            )

        health = error_manager.get_system_health()

        # 错误率应该很高，状态应该是warning或degraded
        assert health.error_rate > 0.05  # 应该超过警告阈值
        assert len(health.active_errors) > 0


class TestSystemMonitor:
    """测试系统监控器"""

    @pytest.fixture
    def monitor(self):
        return SystemMonitor(metrics_history_size=100)

    @pytest.mark.asyncio
    async def test_collect_metrics(self, monitor):
        """测试指标收集"""
        with patch('psutil.cpu_percent', return_value=50.0), \
             patch('psutil.virtual_memory') as mock_memory:
            
            mock_memory.return_value.percent = 60.0
            mock_memory.return_value.available = 1024 * 1024 * 1024  # 1GB
            
            metrics = await monitor.collect_metrics()
            
            assert metrics.cpu_usage == 50.0
            assert metrics.memory_usage == 60.0
            assert metrics.memory_available == 1024 * 1024 * 1024
            assert isinstance(metrics.timestamp, float)

    @pytest.mark.asyncio
    async def test_monitoring_lifecycle(self, monitor):
        """测试监控生命周期"""
        assert not monitor._is_monitoring
        
        # 启动监控
        await monitor.start_monitoring()
        assert monitor._is_monitoring
        assert monitor._monitoring_task is not None
        
        # 等待一小段时间确保监控循环开始
        await asyncio.sleep(0.1)
        
        # 停止监控
        await monitor.stop_monitoring()
        assert not monitor._is_monitoring

    @pytest.mark.asyncio
    async def test_system_diagnosis(self, monitor):
        """测试系统诊断"""
        with patch.object(monitor, 'collect_metrics') as mock_collect:
            # 模拟高CPU使用率
            mock_collect.return_value = SystemMetrics(
                cpu_usage=90.0,
                memory_usage=50.0,
                error_rate=0.1,
                cache_hit_rate=0.3,
                response_time_avg=8.0,
                active_connections=5
            )
            
            result = await monitor.diagnose_system()
            
            assert result.status in ["warning", "critical"]
            assert len(result.issues) > 0
            assert len(result.recommendations) > 0
            assert "CPU使用率过高" in result.issues
            assert "错误率过高" in result.issues
            assert "缓存命中率过低" in result.issues
            assert "响应时间过长" in result.issues

    @pytest.mark.asyncio
    async def test_healthy_system_diagnosis(self, monitor):
        """测试健康系统诊断"""
        with patch.object(monitor, 'collect_metrics') as mock_collect:
            # 模拟健康系统
            mock_collect.return_value = SystemMetrics(
                cpu_usage=30.0,
                memory_usage=40.0,
                error_rate=0.01,
                cache_hit_rate=0.8,
                response_time_avg=1.0,
                active_connections=5
            )
            
            result = await monitor.diagnose_system()
            
            assert result.status == "healthy"
            assert len(result.issues) == 0
            assert len(result.recommendations) == 0

    def test_metrics_summary(self, monitor):
        """测试指标摘要"""
        # 添加一些历史指标
        current_time = time.time()
        for i in range(10):
            metrics = SystemMetrics(
                timestamp=current_time - (i * 60),  # 每分钟一个指标
                cpu_usage=50.0 + i,
                memory_usage=40.0 + i,
                error_rate=0.01 * i,
                response_time_avg=1.0 + i * 0.1
            )
            monitor._add_metrics(metrics)
        
        summary = monitor.get_metrics_summary(minutes=15)
        
        assert summary["data_points"] == 10
        assert "cpu_usage" in summary
        assert "memory_usage" in summary
        assert "error_rate" in summary
        assert "response_time" in summary
        assert summary["cpu_usage"]["min"] == 50.0
        assert summary["cpu_usage"]["max"] == 59.0


class TestIntegratedErrorRecovery:
    """测试集成错误恢复"""

    @pytest.mark.asyncio
    async def test_end_to_end_error_recovery(self):
        """测试端到端错误恢复"""
        error_manager = ErrorRecoveryManager()

        with patch('mcpo.utils.reconnect_manager.reconnect_manager') as mock_reconnect:
            mock_reconnect.attempt_reconnect = AsyncMock(return_value=True)

            # 记录错误
            error_event = await error_manager.record_error(
                "connection_timeout",
                "Connection timeout occurred",
                {"connection_name": "test_conn"},
                ErrorSeverity.HIGH
            )

            # 尝试恢复
            success = await error_manager.attempt_recovery(error_event)

            # 验证结果
            assert success is True
            assert error_event.recovery_attempted is True
            assert error_event.recovery_successful is True

            # 检查系统健康状态
            health = error_manager.get_system_health()
            assert len(health.active_errors) > 0

            # 检查错误统计
            stats = error_manager.get_error_statistics()
            assert stats["total_recovery_attempts"] == 1
            assert stats["recovery_success_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_multiple_error_patterns(self):
        """测试多种错误模式"""
        error_manager = ErrorRecoveryManager()
        
        error_patterns = [
            ("connection_timeout", "Connection timeout"),
            ("server_error", "502 Bad Gateway"),
            ("session_invalid", "Session expired"),
            ("rate_limit", "Rate limit exceeded"),
            ("authentication", "Unauthorized access")
        ]
        
        for pattern, message in error_patterns:
            detected_pattern = error_manager._detect_error_pattern(message)
            assert detected_pattern == pattern
