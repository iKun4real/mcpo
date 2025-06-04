import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from mcpo.main import (
    retry_connection, 
    create_connection_with_timeout, 
    ConnectionManager,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_DELAY,
    DEFAULT_CONNECTION_TIMEOUT
)


class TestRetryConnection:
    """测试连接重试机制"""
    
    @pytest.mark.asyncio
    async def test_successful_connection_on_first_try(self):
        """测试第一次尝试就成功连接"""
        mock_connection = AsyncMock()
        mock_connection.return_value = "success"
        
        result = await retry_connection(mock_connection, connection_name="Test Server")
        
        assert result == "success"
        mock_connection.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_successful_connection_after_retries(self):
        """测试重试后成功连接"""
        mock_connection = AsyncMock()
        mock_connection.side_effect = [
            Exception("Connection failed"),
            Exception("Connection failed"),
            "success"
        ]
        
        result = await retry_connection(
            mock_connection, 
            max_attempts=3, 
            delay=0.1,  # 减少测试时间
            connection_name="Test Server"
        )
        
        assert result == "success"
        assert mock_connection.call_count == 3
    
    @pytest.mark.asyncio
    async def test_all_retries_fail(self):
        """测试所有重试都失败"""
        mock_connection = AsyncMock()
        mock_connection.side_effect = Exception("Connection failed")
        
        with pytest.raises(Exception, match="Connection failed"):
            await retry_connection(
                mock_connection, 
                max_attempts=2, 
                delay=0.1,
                connection_name="Test Server"
            )
        
        assert mock_connection.call_count == 2


class TestConnectionTimeout:
    """测试连接超时机制"""
    
    @pytest.mark.asyncio
    async def test_successful_connection_within_timeout(self):
        """测试在超时时间内成功连接"""
        async def quick_connection():
            await asyncio.sleep(0.1)
            return "success"
        
        result = await create_connection_with_timeout(quick_connection, timeout=1.0)
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_connection_timeout(self):
        """测试连接超时"""
        async def slow_connection():
            await asyncio.sleep(2.0)
            return "success"
        
        with pytest.raises(Exception, match="连接超时"):
            await create_connection_with_timeout(slow_connection, timeout=0.5)


class TestConnectionManager:
    """测试连接管理器"""
    
    def setup_method(self):
        """每个测试方法前的设置"""
        self.manager = ConnectionManager()
        self.mock_session = MagicMock()
    
    def test_register_connection(self):
        """测试注册连接"""
        self.manager.register_connection("test_server", self.mock_session)
        
        assert "test_server" in self.manager.connections
        assert self.manager.connections["test_server"] == self.mock_session
    
    def test_unregister_connection(self):
        """测试注销连接"""
        self.manager.register_connection("test_server", self.mock_session)
        self.manager.unregister_connection("test_server")
        
        assert "test_server" not in self.manager.connections
    
    @pytest.mark.asyncio
    async def test_check_connection_health_success(self):
        """测试健康检查成功"""
        self.mock_session.list_tools = AsyncMock()
        
        result = await self.manager.check_connection_health("test_server", self.mock_session)
        
        assert result is True
        self.mock_session.list_tools.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_check_connection_health_failure(self):
        """测试健康检查失败"""
        self.mock_session.list_tools = AsyncMock(side_effect=Exception("Connection lost"))
        
        result = await self.manager.check_connection_health("test_server", self.mock_session)
        
        assert result is False
        self.mock_session.list_tools.assert_called_once()
    
    def test_record_connection_error(self):
        """测试记录连接错误"""
        self.manager.register_connection("test_server", self.mock_session)

        self.manager.record_connection_error("test_server", "Connection failed")

        status = self.manager.get_connection_status("test_server")
        assert status["status"] == "error"
        assert status["error_count"] == 1
        assert status["last_error"] == "Connection failed"

    def test_record_connection_success(self):
        """测试记录连接成功"""
        self.manager.register_connection("test_server", self.mock_session)

        # 先记录一个错误
        self.manager.record_connection_error("test_server", "Connection failed")
        assert self.manager.get_connection_status("test_server")["error_count"] == 1

        # 然后记录成功
        self.manager.record_connection_success("test_server")

        status = self.manager.get_connection_status("test_server")
        assert status["status"] == "healthy"
        assert status["error_count"] == 0
        assert status["last_error"] is None

    def test_get_connection_status(self):
        """测试获取连接状态"""
        # 未注册的连接
        status = self.manager.get_connection_status("unknown_server")
        assert status["status"] == "unknown"

        # 已注册的连接
        self.manager.register_connection("test_server", self.mock_session)
        status = self.manager.get_connection_status("test_server")
        assert status["status"] == "healthy"
        assert status["error_count"] == 0


class TestConnectionConstants:
    """测试连接配置常量"""
    
    def test_default_values(self):
        """测试默认值是否合理"""
        assert DEFAULT_RETRY_ATTEMPTS == 3
        assert DEFAULT_RETRY_DELAY == 2.0
        assert DEFAULT_CONNECTION_TIMEOUT == 30.0
        assert isinstance(DEFAULT_RETRY_ATTEMPTS, int)
        assert isinstance(DEFAULT_RETRY_DELAY, float)
        assert isinstance(DEFAULT_CONNECTION_TIMEOUT, float)


@pytest.mark.asyncio
async def test_integration_retry_with_timeout():
    """集成测试：重试机制与超时机制结合"""
    call_count = 0
    
    async def flaky_connection():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            await asyncio.sleep(0.1)
            raise Exception("Connection failed")
        await asyncio.sleep(0.1)
        return "success"
    
    result = await retry_connection(
        lambda: create_connection_with_timeout(flaky_connection, timeout=1.0),
        max_attempts=3,
        delay=0.1,
        connection_name="Integration Test Server"
    )
    
    assert result == "success"
    assert call_count == 3
