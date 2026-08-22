"""共享事件日志 — 供graph.py和server_agui.py使用"""
import time as _time
from typing import Any

_event_log: list[dict] = []
_MAX = 200

def log_event(event_type: str, data: dict[str, Any]):
    """记录事件"""
    _event_log.append({
        "type": event_type,
        "timestamp": _time.time(),
        "data": data,
    })
    if len(_event_log) > _MAX:
        _event_log.pop(0)

def get_events(after: float = 0) -> list[dict]:
    """获取事件（支持增量）"""
    if after > 0:
        return [e for e in _event_log if e["timestamp"] > after]
    return _event_log[-50:]
