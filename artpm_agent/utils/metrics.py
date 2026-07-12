"""
生产级监控模块
"""
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import json
from pathlib import Path

@dataclass
class MetricEvent:
    """指标事件"""
    timestamp: datetime
    metric_type: str  # api_call, skill_execution, db_query
    name: str
    duration_ms: float
    status: str  # success, error
    metadata: dict

class MetricsCollector:
    """指标收集器"""

    def __init__(self, output_dir: str = "./logs/metrics"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events = []

    @contextmanager
    def track(self, metric_type: str, name: str, **metadata):
        """追踪执行时间"""
        start = time.perf_counter()
        status = "success"
        try:
            yield
        except Exception as e:
            status = "error"
            metadata["error"] = str(e)
            raise
        finally:
            duration = (time.perf_counter() - start) * 1000
            event = MetricEvent(
                timestamp=datetime.now(),
                metric_type=metric_type,
                name=name,
                duration_ms=duration,
                status=status,
                metadata=metadata
            )
            self._record(event)

    def _record(self, event: MetricEvent):
        """记录事件"""
        self.events.append(event)
        # 写入日志文件
        log_file = self.output_dir / f"metrics_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(log_file, 'a', encoding='utf-8') as f:
            json.dump({
                "timestamp": event.timestamp.isoformat(),
                "type": event.metric_type,
                "name": event.name,
                "duration_ms": event.duration_ms,
                "status": event.status,
                "metadata": event.metadata
            }, f, ensure_ascii=False)
            f.write('\n')

    def get_summary(self):
        """获取统计摘要"""
        if not self.events:
            return {}

        by_type = {}
        for event in self.events:
            key = f"{event.metric_type}.{event.name}"
            if key not in by_type:
                by_type[key] = {"count": 0, "total_ms": 0, "errors": 0}
            by_type[key]["count"] += 1
            by_type[key]["total_ms"] += event.duration_ms
            if event.status == "error":
                by_type[key]["errors"] += 1

        for key, stats in by_type.items():
            stats["avg_ms"] = stats["total_ms"] / stats["count"]

        return by_type

# 全局实例
metrics = MetricsCollector()

# 使用示例:
# with metrics.track("skill_execution", "quote_calculator", project_id=123):
#     result = calculate_quote(...)
