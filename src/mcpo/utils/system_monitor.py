"""
系统监控和诊断工具
提供实时系统状态监控、性能分析和问题诊断
"""

import asyncio
import logging
import time
import psutil
import gc
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import json

logger = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    """系统指标"""
    timestamp: float = field(default_factory=time.time)
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    memory_available: int = 0
    active_connections: int = 0
    cache_hit_rate: float = 0.0
    error_rate: float = 0.0
    response_time_avg: float = 0.0
    gc_collections: int = 0


@dataclass
class DiagnosticResult:
    """诊断结果"""
    timestamp: float = field(default_factory=time.time)
    status: str = "unknown"
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    metrics: Optional[SystemMetrics] = None


class SystemMonitor:
    """系统监控器"""
    
    def __init__(self, metrics_history_size: int = 1000):
        self.metrics_history_size = metrics_history_size
        self.metrics_history: List[SystemMetrics] = []
        self._monitoring_task: Optional[asyncio.Task] = None
        self._monitoring_interval = 30  # 30秒监控间隔
        self._is_monitoring = False
        
        # 阈值配置
        self.thresholds = {
            "cpu_usage_warning": 70.0,
            "cpu_usage_critical": 90.0,
            "memory_usage_warning": 80.0,
            "memory_usage_critical": 95.0,
            "error_rate_warning": 0.05,
            "error_rate_critical": 0.1,
            "response_time_warning": 5.0,
            "response_time_critical": 10.0,
            "cache_hit_rate_warning": 0.5,
        }

    async def start_monitoring(self):
        """开始监控"""
        if self._is_monitoring:
            logger.warning("监控已经在运行")
            return
        
        self._is_monitoring = True
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info("系统监控已启动")

    async def stop_monitoring(self):
        """停止监控"""
        if not self._is_monitoring:
            return
        
        self._is_monitoring = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        
        logger.info("系统监控已停止")

    async def _monitoring_loop(self):
        """监控循环"""
        while self._is_monitoring:
            try:
                metrics = await self.collect_metrics()
                self._add_metrics(metrics)
                
                # 检查是否需要告警
                await self._check_alerts(metrics)
                
                await asyncio.sleep(self._monitoring_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控循环出错: {str(e)}")
                await asyncio.sleep(5)

    async def collect_metrics(self) -> SystemMetrics:
        """收集系统指标"""
        try:
            # 系统资源指标
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            memory_usage = memory.percent
            memory_available = memory.available
            
            # 应用指标
            active_connections = await self._get_active_connections()
            cache_hit_rate = await self._get_cache_hit_rate()
            error_rate = await self._get_error_rate()
            response_time_avg = await self._get_avg_response_time()
            
            # GC指标
            gc_collections = sum(gc.get_stats()[i]['collections'] for i in range(len(gc.get_stats())))
            
            return SystemMetrics(
                cpu_usage=cpu_usage,
                memory_usage=memory_usage,
                memory_available=memory_available,
                active_connections=active_connections,
                cache_hit_rate=cache_hit_rate,
                error_rate=error_rate,
                response_time_avg=response_time_avg,
                gc_collections=gc_collections
            )
            
        except Exception as e:
            logger.error(f"收集系统指标失败: {str(e)}")
            return SystemMetrics()

    async def _get_active_connections(self) -> int:
        """获取活跃连接数"""
        try:
            from .reconnect_manager import reconnect_manager
            return len(reconnect_manager.connections)
        except Exception:
            return 0

    async def _get_cache_hit_rate(self) -> float:
        """获取缓存命中率"""
        try:
            from .cache import cache_manager
            stats = cache_manager.default_cache.get_stats()
            total_requests = stats.get('total_requests', 0)
            hits = stats.get('hits', 0)
            
            if total_requests > 0:
                return hits / total_requests
            return 0.0
        except Exception:
            return 0.0

    async def _get_error_rate(self) -> float:
        """获取错误率"""
        try:
            from .error_recovery import error_recovery_manager
            return error_recovery_manager.system_health.error_rate
        except Exception:
            return 0.0

    async def _get_avg_response_time(self) -> float:
        """获取平均响应时间"""
        try:
            from .performance import performance_monitor
            metrics = performance_monitor.get_metrics()
            return metrics.get('avg_response_time', 0.0)
        except Exception:
            return 0.0

    def _add_metrics(self, metrics: SystemMetrics):
        """添加指标到历史记录"""
        self.metrics_history.append(metrics)
        
        # 限制历史记录大小
        if len(self.metrics_history) > self.metrics_history_size:
            self.metrics_history = self.metrics_history[-self.metrics_history_size:]

    async def _check_alerts(self, metrics: SystemMetrics):
        """检查告警条件"""
        alerts = []
        
        # CPU使用率告警
        if metrics.cpu_usage > self.thresholds["cpu_usage_critical"]:
            alerts.append(f"CPU使用率过高: {metrics.cpu_usage:.1f}%")
        elif metrics.cpu_usage > self.thresholds["cpu_usage_warning"]:
            alerts.append(f"CPU使用率警告: {metrics.cpu_usage:.1f}%")
        
        # 内存使用率告警
        if metrics.memory_usage > self.thresholds["memory_usage_critical"]:
            alerts.append(f"内存使用率过高: {metrics.memory_usage:.1f}%")
        elif metrics.memory_usage > self.thresholds["memory_usage_warning"]:
            alerts.append(f"内存使用率警告: {metrics.memory_usage:.1f}%")
        
        # 错误率告警
        if metrics.error_rate > self.thresholds["error_rate_critical"]:
            alerts.append(f"错误率过高: {metrics.error_rate:.3f}")
        elif metrics.error_rate > self.thresholds["error_rate_warning"]:
            alerts.append(f"错误率警告: {metrics.error_rate:.3f}")
        
        # 响应时间告警
        if metrics.response_time_avg > self.thresholds["response_time_critical"]:
            alerts.append(f"响应时间过长: {metrics.response_time_avg:.2f}s")
        elif metrics.response_time_avg > self.thresholds["response_time_warning"]:
            alerts.append(f"响应时间警告: {metrics.response_time_avg:.2f}s")
        
        # 缓存命中率告警
        if metrics.cache_hit_rate < self.thresholds["cache_hit_rate_warning"]:
            alerts.append(f"缓存命中率过低: {metrics.cache_hit_rate:.2f}")
        
        # 记录告警
        for alert in alerts:
            logger.warning(f"系统告警: {alert}")

    async def diagnose_system(self) -> DiagnosticResult:
        """系统诊断"""
        try:
            current_metrics = await self.collect_metrics()
            issues = []
            recommendations = []
            
            # 性能问题诊断
            if current_metrics.cpu_usage > 80:
                issues.append("CPU使用率过高")
                recommendations.append("检查是否有CPU密集型任务，考虑优化算法或增加并发限制")
            
            if current_metrics.memory_usage > 85:
                issues.append("内存使用率过高")
                recommendations.append("检查内存泄漏，清理缓存，或增加内存限制")
            
            if current_metrics.error_rate > 0.05:
                issues.append("错误率过高")
                recommendations.append("检查错误日志，修复连接问题，或调整重试策略")
            
            if current_metrics.cache_hit_rate < 0.5:
                issues.append("缓存命中率过低")
                recommendations.append("调整缓存策略，增加缓存大小，或优化缓存键生成")
            
            if current_metrics.response_time_avg > 5.0:
                issues.append("响应时间过长")
                recommendations.append("优化数据库查询，增加连接池大小，或启用缓存")
            
            # 连接问题诊断
            if current_metrics.active_connections == 0:
                issues.append("没有活跃连接")
                recommendations.append("检查MCP服务器连接配置和网络连通性")
            
            # 确定整体状态
            if len(issues) == 0:
                status = "healthy"
            elif len(issues) <= 2:
                status = "warning"
            else:
                status = "critical"
            
            return DiagnosticResult(
                status=status,
                issues=issues,
                recommendations=recommendations,
                metrics=current_metrics
            )
            
        except Exception as e:
            logger.error(f"系统诊断失败: {str(e)}")
            return DiagnosticResult(
                status="error",
                issues=[f"诊断过程出错: {str(e)}"],
                recommendations=["检查系统日志，重启监控服务"]
            )

    def get_metrics_summary(self, minutes: int = 60) -> Dict[str, Any]:
        """获取指标摘要"""
        try:
            cutoff_time = time.time() - (minutes * 60)
            recent_metrics = [m for m in self.metrics_history if m.timestamp > cutoff_time]
            
            if not recent_metrics:
                return {"error": "没有足够的历史数据"}
            
            # 计算统计值
            cpu_values = [m.cpu_usage for m in recent_metrics]
            memory_values = [m.memory_usage for m in recent_metrics]
            error_rates = [m.error_rate for m in recent_metrics]
            response_times = [m.response_time_avg for m in recent_metrics]
            
            return {
                "time_range_minutes": minutes,
                "data_points": len(recent_metrics),
                "cpu_usage": {
                    "avg": sum(cpu_values) / len(cpu_values),
                    "max": max(cpu_values),
                    "min": min(cpu_values)
                },
                "memory_usage": {
                    "avg": sum(memory_values) / len(memory_values),
                    "max": max(memory_values),
                    "min": min(memory_values)
                },
                "error_rate": {
                    "avg": sum(error_rates) / len(error_rates),
                    "max": max(error_rates),
                    "current": recent_metrics[-1].error_rate if recent_metrics else 0
                },
                "response_time": {
                    "avg": sum(response_times) / len(response_times),
                    "max": max(response_times),
                    "current": recent_metrics[-1].response_time_avg if recent_metrics else 0
                },
                "current_connections": recent_metrics[-1].active_connections if recent_metrics else 0
            }
            
        except Exception as e:
            logger.error(f"获取指标摘要失败: {str(e)}")
            return {"error": str(e)}


# 全局系统监控器
system_monitor = SystemMonitor()
