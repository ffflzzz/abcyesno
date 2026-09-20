# -*- coding: utf-8 -*-
"""supervisor 创作链诊断：经 langgraph_sdk 连 dev 服务，流式打印事件与产物落盘。

用法：
    python scripts/diag_orch.py <项目> [--url http://127.0.0.1:2024]
                                      [--max-events N] [--timeout S]

前提：dev 已启动
    SHORTDRAMA_V5_PROJECT=<项目> langgraph dev --config v5/langgraph.json

说明（2026-09-12 改造）：原为静态链诊断（v5.graph.build + astream_events）；
静态链已废弃，改为经 Agent Protocol 驱动 supervisor 图，并**逐轮打印
async_tasks channel**（派发是否真实注册的第一手证据）。
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _arg(flag: str, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


async def main() -> None:
    from langgraph_sdk import get_client

    # ⚠️ 原默认值 `lane-erhu` 已随 2026-09-14 的老架构清理删除；请显式传项目名。
    project = next((a for a in sys.argv[1:] if not a.startswith("--")), "<请显式传项目名>")
    url = _arg("--url", "http://127.0.0.1:2024")
    max_events = int(_arg("--max-events", "200"))
    timeout = float(_arg("--timeout", "900"))
    goal = _arg("--goal", "按依赖序推进创作链；每轮只 check 一次。")

    client = get_client(url=url)
    thread = await client.threads.create()
    print("[diag] thread:", thread["thread_id"])

    run = await client.runs.create(thread["thread_id"], "supervisor",
                                   input={"messages": [{"role": "user", "content": goal}]},
                                   config={"recursion_limit": 80})
    t0 = time.time()
    n = 0
    async for ev in client.runs.stream(thread["thread_id"], run["run_id"],
                                        stream_mode=["updates", "messages"]):
        n += 1
        if n > max_events or time.time() - t0 > timeout:
            print("\n[diag] 达到上限（events=%d, %.0fs）" % (n, time.time() - t0))
            break
        kind = getattr(ev, "event", "")
        data = getattr(ev, "data", None)
        print("[%5.1fs] %-22s %s" % (time.time() - t0, kind, str(data)[:150]))

    st = await client.threads.get_state(thread["thread_id"])
    at = (st.get("values") or {}).get("async_tasks") or {}
    tasks = list(at.values()) if isinstance(at, dict) else []
    print("\n[diag] async_tasks: %d 条" % len(tasks))
    for t in tasks:
        print("   %-16s %-9s thread=%s" % (t.get("agent_name"), t.get("status"),
                                           str(t.get("thread_id"))[:8]))
    print("[diag] 产物目录:", Path("projects") / project)


asyncio.run(main())
