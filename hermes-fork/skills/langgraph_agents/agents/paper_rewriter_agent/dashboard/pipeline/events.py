"""事件发射器 — 供UI实时监听pipeline执行"""
from __future__ import annotations
import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class PipelineEvent:
    """一次pipeline事件"""
    timestamp: float
    node_id: str               # 当前执行的节点名
    event_type: str            # node_start | node_end | edge_traverse | state_update | error | complete
    message: str               # 人类可读描述
    state_snapshot: dict       # 状态快照（精简版，不含原文全文）
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class EventEmitter:
    """SSE 事件总线，支持多客户端订阅，线程安全"""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """注册事件循环，使 emit_sync 可从后台线程安全唤醒"""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def emit(self, event: PipelineEvent):
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def emit_sync(self, event: PipelineEvent):
        """同步版本，供非async节点（后台线程）使用。
        用 call_soon_threadsafe 确保 SSE 的 await get() 能被唤醒。"""
        dead = []
        for q in self._subscribers:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(q.put_nowait, event)
                else:
                    q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)


# 全局单例
emitter = EventEmitter()


def make_state_snapshot(state: dict) -> dict:
    """从state生成精简快照（去掉原文全文等大字段，处理LangChain消息对象）"""
    snapshot = {}
    for k, v in state.items():
        if k == "original_text":
            snapshot[k] = f"[{len(v)} chars]" if v else ""
        elif k == "full_rewrite":
            snapshot[k] = f"[{len(v)} chars]" if v else ""
        elif k == "chapters":
            snapshot[k] = {ck: f"[{len(cv)} chars]" for ck, cv in (v or {}).items()}
        elif k == "messages":
            # LangChain Message 对象不可直接 JSON 序列化，转为摘要
            msgs = v or []
            snapshot[k] = f"[{len(msgs)} messages]"
        elif isinstance(v, str) and len(v) > 500:
            snapshot[k] = v[:500] + "..."
        elif hasattr(v, "content") and hasattr(v, "type"):
            # LangChain Message 对象（HumanMessage, AIMessage 等）
            snapshot[k] = f"[{type(v).__name__}: {str(v.content)[:100]}]"
        else:
            snapshot[k] = v
    return snapshot


def fire_node_start(node_id: str, message: str, state: dict, **meta):
    emitter.emit_sync(PipelineEvent(
        timestamp=time.time(),
        node_id=node_id,
        event_type="node_start",
        message=message,
        state_snapshot=make_state_snapshot(state),
        metadata=meta,
    ))


def fire_node_end(node_id: str, message: str, state: dict, **meta):
    emitter.emit_sync(PipelineEvent(
        timestamp=time.time(),
        node_id=node_id,
        event_type="node_end",
        message=message,
        state_snapshot=make_state_snapshot(state),
        metadata=meta,
    ))


def fire_state_update(node_id: str, message: str, state: dict, **meta):
    emitter.emit_sync(PipelineEvent(
        timestamp=time.time(),
        node_id=node_id,
        event_type="state_update",
        message=message,
        state_snapshot=make_state_snapshot(state),
        metadata=meta,
    ))


def fire_error(node_id: str, message: str, state: dict, **meta):
    emitter.emit_sync(PipelineEvent(
        timestamp=time.time(),
        node_id=node_id,
        event_type="error",
        message=message,
        state_snapshot=make_state_snapshot(state),
        metadata=meta,
    ))


def fire_complete(message: str, state: dict, **meta):
    emitter.emit_sync(PipelineEvent(
        timestamp=time.time(),
        node_id="__end__",
        event_type="complete",
        message=message,
        state_snapshot=make_state_snapshot(state),
        metadata=meta,
    ))


def fire_llm_token(node_id: str, token_type: str, content: str, chapter: str = ""):
    """发射LLM流式token事件。token_type: 'token' 或 'reasoning'"""
    emitter.emit_sync(PipelineEvent(
        timestamp=time.time(),
        node_id=node_id,
        event_type="llm_token",
        message=content,
        state_snapshot={},
        metadata={"token_type": token_type, "chapter": chapter},
    ))
