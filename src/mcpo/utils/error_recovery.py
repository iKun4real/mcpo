"""
错误恢复和监控系统
提供全面的错误处理、恢复机制和系统监控
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import json

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """错误严重程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecoveryAction(Enum):
    """恢复动作类型"""
    RETRY = "retry"
    RECONNECT = "reconnect"
    RESET_SESSION = "reset_session"
    FALLBACK = "fallback"
    ESCALATE = "escalate"


@dataclass
class ErrorEvent:
    """错误事件"""
    timestamp: float
    error_type: str
    error_message: str
    severity: ErrorSeverity
    context: Dict[str, Any] = field(default_factory=dict)
    recovery_attempted: bool = False
    recovery_successful: bool = False
    recovery_action: Optional[RecoveryAction] = None


@dataclass
class SystemHealth:
    """系统健康状态"""
    overall_status: str = "healthy"
    connection_health: Dict[str, bool] = field(default_factory=dict)
    error_rate: float = 0.0
    last_check: float = field(default_factory=time.time)
    active_errors: List[ErrorEvent] = field(default_factory=list)
    performance_metrics: Dict[str, float] = field(default_factory=dict)


class ErrorRecoveryManager:
    """错误恢复管理器"""
    
    def __init__(self, max_error_history: int = 1000):
        self.max_error_history = max_error_history
        self.error_history: List[ErrorEvent] = []
        self.recovery_strategies: Dict[str, Callable] = {}
        self.system_health = SystemHealth()
        self._lock = asyncio.Lock()
        
        # 错误模式检测
        self.error_patterns = {
            "connection_timeout": ["timeout", "connection", "network"],
            "server_error": ["500", "502", "503", "504"],
            "session_invalid": ["session", "invalid", "expired"],
            "rate_limit": ["rate", "limit", "throttle"],
            "authentication": ["auth", "unauthorized", "forbidden"]
        }
        
        # 默认恢复策略
        self._register_default_strategies()

    def _register_default_strategies(self):
        """注册默认恢复策略"""
        self.recovery_strategies.update({
            "connection_timeout": self._handle_connection_timeout,
            "server_error": self._handle_server_error,
            "session_invalid": self._handle_session_invalid,
            "rate_limit": self._handle_rate_limit,
            "authentication": self._handle_authentication_error
        })

    async def record_error(self, 
                          error_type: str, 
                          error_message: str, 
                          context: Dict[str, Any] = None,
                          severity: ErrorSeverity = ErrorSeverity.MEDIUM) -> ErrorEvent:
        """记录错误事件"""
        async with self._lock:
            event = ErrorEvent(
                timestamp=time.time(),
                error_type=error_type,
                error_message=error_message,
                severity=severity,
                context=context or {}
            )
            
            self.error_history.append(event)
            
            # 限制历史记录大小
            if len(self.error_history) > self.max_error_history:
                self.error_history = self.error_history[-self.max_error_history:]
            
            # 更新系统健康状态
            await self._update_system_health(event)
            
            logger.warning(f"记录错误事件: {error_type} - {error_message}")
            return event

    async def attempt_recovery(self, error_event: ErrorEvent) -> bool:
        """尝试错误恢复"""
        try:
            # 检测错误模式
            pattern = self._detect_error_pattern(error_event.error_message)
            
            if pattern and pattern in self.recovery_strategies:
                logger.info(f"尝试恢复错误: {pattern}")
                error_event.recovery_attempted = True
                
                # 执行恢复策略
                success = await self.recovery_strategies[pattern](error_event)
                error_event.recovery_successful = success
                
                if success:
                    logger.info(f"错误恢复成功: {pattern}")
                else:
                    logger.warning(f"错误恢复失败: {pattern}")
                
                return success
            else:
                logger.warning(f"未找到匹配的恢复策略: {error_event.error_type}")
                return False
                
        except Exception as e:
            logger.error(f"执行错误恢复时发生异常: {str(e)}")
            return False

    def _detect_error_pattern(self, error_message: str) -> Optional[str]:
        """检测错误模式"""
        error_lower = error_message.lower()
        
        for pattern, keywords in self.error_patterns.items():
            if any(keyword in error_lower for keyword in keywords):
                return pattern
        
        return None

    async def _handle_connection_timeout(self, error_event: ErrorEvent) -> bool:
        """处理连接超时错误"""
        try:
            # 从重连管理器获取连接名
            connection_name = error_event.context.get("connection_name")
            if not connection_name:
                return False
            
            # 尝试重连
            from .reconnect_manager import reconnect_manager
            success = await reconnect_manager.attempt_reconnect(connection_name)
            
            error_event.recovery_action = RecoveryAction.RECONNECT
            return success
            
        except Exception as e:
            logger.error(f"处理连接超时错误失败: {str(e)}")
            return False

    async def _handle_server_error(self, error_event: ErrorEvent) -> bool:
        """处理服务器错误"""
        try:
            # 对于5xx错误，等待一段时间后重试
            await asyncio.sleep(2)
            error_event.recovery_action = RecoveryAction.RETRY
            return True
            
        except Exception as e:
            logger.error(f"处理服务器错误失败: {str(e)}")
            return False

    async def _handle_session_invalid(self, error_event: ErrorEvent) -> bool:
        """处理会话无效错误"""
        try:
            connection_name = error_event.context.get("connection_name")
            if not connection_name:
                return False
            
            # 重置会话
            from .reconnect_manager import reconnect_manager
            success = await reconnect_manager.attempt_reconnect(connection_name)
            
            error_event.recovery_action = RecoveryAction.RESET_SESSION
            return success
            
        except Exception as e:
            logger.error(f"处理会话无效错误失败: {str(e)}")
            return False

    async def _handle_rate_limit(self, error_event: ErrorEvent) -> bool:
        """处理速率限制错误"""
        try:
            # 等待更长时间
            await asyncio.sleep(5)
            error_event.recovery_action = RecoveryAction.RETRY
            return True
            
        except Exception as e:
            logger.error(f"处理速率限制错误失败: {str(e)}")
            return False

    async def _handle_authentication_error(self, error_event: ErrorEvent) -> bool:
        """处理认证错误"""
        try:
            # 认证错误通常需要人工干预
            error_event.recovery_action = RecoveryAction.ESCALATE
            logger.error("检测到认证错误，需要人工干预")
            return False
            
        except Exception as e:
            logger.error(f"处理认证错误失败: {str(e)}")
            return False

    async def _update_system_health(self, error_event: ErrorEvent):
        """更新系统健康状态"""
        try:
            # 计算错误率
            recent_errors = [e for e in self.error_history 
                           if time.time() - e.timestamp < 300]  # 最近5分钟
            self.system_health.error_rate = len(recent_errors) / 300.0
            
            # 更新整体状态
            if self.system_health.error_rate > 0.1:  # 每秒超过0.1个错误
                self.system_health.overall_status = "degraded"
            elif self.system_health.error_rate > 0.05:
                self.system_health.overall_status = "warning"
            else:
                self.system_health.overall_status = "healthy"
            
            # 添加到活跃错误列表
            if error_event.severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
                self.system_health.active_errors.append(error_event)
                
                # 限制活跃错误数量
                if len(self.system_health.active_errors) > 50:
                    self.system_health.active_errors = self.system_health.active_errors[-50:]
            
            self.system_health.last_check = time.time()
            
        except Exception as e:
            logger.error(f"更新系统健康状态失败: {str(e)}")

    def get_system_health(self) -> SystemHealth:
        """获取系统健康状态"""
        return self.system_health

    def get_error_statistics(self) -> Dict[str, Any]:
        """获取错误统计信息"""
        try:
            recent_errors = [e for e in self.error_history 
                           if time.time() - e.timestamp < 3600]  # 最近1小时
            
            error_types = {}
            recovery_success_rate = 0
            total_recovery_attempts = 0
            
            for error in recent_errors:
                error_types[error.error_type] = error_types.get(error.error_type, 0) + 1
                
                if error.recovery_attempted:
                    total_recovery_attempts += 1
                    if error.recovery_successful:
                        recovery_success_rate += 1
            
            if total_recovery_attempts > 0:
                recovery_success_rate = recovery_success_rate / total_recovery_attempts
            
            return {
                "total_errors_last_hour": len(recent_errors),
                "error_types": error_types,
                "recovery_success_rate": recovery_success_rate,
                "total_recovery_attempts": total_recovery_attempts,
                "current_error_rate": self.system_health.error_rate,
                "system_status": self.system_health.overall_status
            }
            
        except Exception as e:
            logger.error(f"获取错误统计失败: {str(e)}")
            return {}


# 全局错误恢复管理器
error_recovery_manager = ErrorRecoveryManager()
