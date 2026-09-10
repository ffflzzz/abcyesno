"""Regression tests for the WS read-loop dispatch decoupling (2026-09-10).

Context
-------
``handle_ws`` used to do ``resp = await asyncio.to_thread(server.dispatch, ...)``
per inbound frame, which **serially waited** for each RPC to finish before
reading the next one. Handlers outside ``_LONG_HANDLERS`` (notably
``prompt.submit``, ``session.steer``, ``session.status``) run *inline* inside
``dispatch``, so one slow RPC blocked every later RPC in the socket buffer.

Real-world symptom (WeChat bridge): during a long agent turn the gateway
reported ``gateway request timeout: prompt.submit`` at a fixed 120s cadence —
and ``session.steer`` (a pure in-memory string write) timed out at 15s at the
same moment, which is only possible if the read loop itself stalled. The
crashes were paired 125s apart (120s timeout + 5s backoff), proving a
deterministic stall rather than load-dependent slowness.

Fix: submit ``dispatch`` to a dedicated executor and return to
``receive_text()`` immediately; the worker writes its own response through the
transport, exactly like the existing ``_LONG_HANDLERS`` path.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tui_gateway import server  # noqa: E402
from tui_gateway import ws as ws_mod  # noqa: E402


class _FakeWS:
    """Minimal WebSocket double: queued inbound frames, recorded outbound.

    Once the queue drains, ``receive_text`` blocks forever (like a real idle
    socket) instead of raising — a real client that has sent its frames and is
    waiting for replies does not disconnect.
    """

    def __init__(self, inbound: list[str]) -> None:
        self._inbound = list(inbound)
        self.sent: list[str] = []
        self.closed = False
        self.scope: dict = {}
        self._idle = asyncio.Event()

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        # Idle: wait until the test cancels us.
        await self._idle.wait()
        raise RuntimeError("idle socket resumed")  # pragma: no cover

    async def send_text(self, line: str) -> None:
        self.sent.append(line)

    async def close(self) -> None:
        self.closed = True


def test_ws_dispatch_pool_is_separate_and_sized() -> None:
    """The read-loop executor must exist and be big enough for light RPCs."""
    assert ws_mod._WS_DISPATCH_POOL is not None
    assert ws_mod._WS_DISPATCH_POOL_WORKERS >= 4
    # Must NOT be the asyncio default pool (that one is shared with
    # asyncio.to_thread callers elsewhere and would re-introduce contention).
    assert "ws-dispatch" in (
        ws_mod._WS_DISPATCH_POOL._thread_name_prefix
        if hasattr(ws_mod._WS_DISPATCH_POOL, "_thread_name_prefix")
        else "ws-dispatch"
    )


def test_slow_dispatch_does_not_block_later_rpcs() -> None:
    """A slow handler must not delay the dispatch of subsequent frames.

    This is the core regression: pre-fix, frame B could not even *start*
    dispatching until frame A's handler returned, because the read loop
    awaited each ``dispatch`` before reading again.
    """
    started: list[str] = []
    finished: list[str] = []
    release = threading.Event()
    fast_done = threading.Event()

    def slow_handler(rid, params):
        started.append("slow")
        # Simulate a handler stuck behind a long turn.
        release.wait(timeout=5)
        finished.append("slow")
        return {"jsonrpc": "2.0", "id": rid, "result": {"ok": "slow"}}

    def fast_handler(rid, params):
        started.append("fast")
        finished.append("fast")
        fast_done.set()
        return {"jsonrpc": "2.0", "id": rid, "result": {"ok": "fast"}}

    async def run() -> None:
        loop = asyncio.get_running_loop()
        order: list[str] = []

        def _slow() -> None:
            order.append("slow-start")
            slow_handler("1", {})
            order.append("slow-end")

        def _fast() -> None:
            order.append("fast-start")
            fast_handler("2", {})
            order.append("fast-end")

        loop.run_in_executor(ws_mod._WS_DISPATCH_POOL, _slow)
        # Give the slow worker a moment to occupy a thread.
        await asyncio.sleep(0.05)
        # The fast frame must be able to run concurrently on another thread.
        loop.run_in_executor(ws_mod._WS_DISPATCH_POOL, _fast)

        # The fast handler must complete while the slow one is still blocked.
        ok = await asyncio.get_running_loop().run_in_executor(
            None, fast_done.wait, 2.0
        )
        assert ok, "fast handler never ran while slow handler was blocked"
        assert "fast-end" in order
        assert "slow-end" not in order, "slow handler released too early"

        release.set()
        deadline = time.time() + 3
        while "slow-end" not in order and time.time() < deadline:
            await asyncio.sleep(0.02)
        assert "slow-end" in order

    asyncio.run(run())


def test_ws_read_loop_drains_all_frames_after_slow_first() -> None:
    """End-to-end: two frames queued behind a slow first frame both run.

    Drives ``handle_ws`` with a fake WS whose first RPC blocks. The second
    frame must still reach dispatch and produce a response — pre-fix it sat
    unread until the first finished (or the client gave up).
    """
    calls: list[str] = []
    release = threading.Event()
    fast_seen = threading.Event()

    def fake_dispatch(req, transport=None):
        method = req.get("method")
        calls.append(method)
        if method == "slow.method":
            release.wait(timeout=5)
        if method == "fast.method":
            fast_seen.set()
        resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"m": method}}
        if transport is not None:
            transport.write(resp)
        return resp

    import json

    inbound = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slow.method"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "fast.method"}),
    ]
    fake = _FakeWS(inbound)

    original = server.dispatch
    server.dispatch = fake_dispatch
    try:

        async def run() -> None:
            task = asyncio.ensure_future(ws_mod.handle_ws(fake))
            # The core assertion: the fast frame reaches dispatch while the
            # slow frame is still blocked in its handler. Pre-fix, the read
            # loop awaited the slow dispatch, so `fast.method` never appeared
            # until the 5s guard released.
            loop = asyncio.get_running_loop()
            got_fast = await loop.run_in_executor(None, fast_seen.wait, 3.0)
            assert "slow.method" in calls, f"first frame was never dispatched: {calls}"
            assert got_fast, (
                "second frame never dispatched — read loop was blocked "
                f"behind the slow handler (calls={calls})"
            )
            release.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError):
                pass

        asyncio.run(run())
    finally:
        server.dispatch = original
