#!/usr/bin/env python3
"""manjucraft_agent series 冒烟测试（mock 模式，零额度）。

用法:
  hermes-fork/.venv/Scripts/python.exe scripts/smoke_manjucraft.py

覆盖: series 多集拆分(plan_episodes)、每集 13 节点流水线、series 循环
(finalize_episode → parse_script 直到 current_episode == total_episodes)、
character_bible 跨集锁定、审批门(interrupt)在 auto_approve 下自动通过。
"""
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILLS = os.path.join(REPO, "hermes-fork", "skills")

# mock + 假 key（mock 下不真调 Agnes，但 build_initial_state 会检查 key 存在）
os.environ["MANJUCRAFT_AGENT_MOCK"] = "1"
os.environ["AGNES_API_KEY"] = os.environ.get("AGNES_API_KEY") or "dummy-key-for-smoke"
os.environ.setdefault("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")

sys.path.insert(0, SKILLS)
sys.path.insert(0, os.path.join(REPO, "hermes-fork"))

from langgraph_agents.langgraph_runtime import run_agent, discover_agents  # noqa: E402

TOTAL = 3

print("=== discover_agents ===", discover_agents())

events = []
trace = []

def on_event(etype, payload):
    events.append((etype, payload))
    if etype == "workflow.trace":
        trace.append((payload.get("node"), payload.get("status"), payload.get("episode")))
    elif etype in ("workflow.started", "workflow.graph", "workflow.done", "workflow.error"):
        print(f"[{etype}] {json.dumps(payload, ensure_ascii=False)[:180]}")
    elif etype == "workflow.approval":
        print(f"[approval] gate={payload.get('gate_id')} node={payload.get('node')}")

series_script = (
    "第一集：主角阿杰在小镇发现一只会说话的黑猫。"
    "第二集：阿杰跟着黑猫进入森林，遇到会飞的狐狸。"
    "第三集：他们一起打败破坏森林的坏蛋，守护了家园。"
)

t0 = time.time()
result = run_agent(
    "manjucraft_agent",
    input_obj={
        "mode": "series",
        "series_script": series_script,
        "total_episodes": TOTAL,
        "style": "二次元",
        "consistency_policy": "lock_bible",
        "resolution": "1080x1920",
        "sec_per_shot": 3,
    },
    auto_approve=True,
    on_event=on_event,
    run_id="wf-smoke-series",
)
dt = time.time() - t0

print(f"\n=== RESULT (elapsed {dt:.1f}s) ===")
if result.get("error"):
    print("ERROR:", result["error"])
    sys.exit(1)
print("summary:", result.get("output", ""))
artifacts = result.get("artifacts") or []
print("artifacts:", len(artifacts))
for a in artifacts:
    print("  -", a.get("label"), "->", a.get("path", ""))

state = result.get("state") or {}
eps = state.get("episode_results") or []
print(f"\nepisodes: {len(eps)}/{state.get('total_episodes')}")
for er in eps:
    print(f"  ep{er.get('episode')} video={bool(er.get('final_video_path'))} "
          f"jianying={bool(er.get('jianying_draft_path'))} assets={bool(er.get('assets_zip_path'))}")
bible = state.get("character_bible") or []
print("character_bible 锁定角色数:", len(bible))

# 校验循环：parse_script 应出现 TOTAL 次（每集一次）。trace 每个节点发
# running+done 两次，只统计 running。
parse_runs = sum(1 for n, s, _ in trace if n == "parse_script" and s == "running")
finalize_eps = sum(1 for n, s, _ in trace if n == "finalize_episode" and s == "running")
# 审批门次数校验：首集 3 门(first_frame/each_scene/end) + 续集每集 1 门(episode_ready)
approvals = [p.get("gate_id") for t, p in events if t == "workflow.approval"]
first_frame = approvals.count("first_frame")
each_scene = approvals.count("each_scene")
end = approvals.count("end")
episode_ready = approvals.count("episode_ready")
print(f"\n循环校验: parse_script 出现 {parse_runs} 次, finalize_episode 出现 {finalize_eps} 次 (期望各 {TOTAL})")
print(f"审批门校验: first_frame={first_frame} each_scene={each_scene} end={end} episode_ready={episode_ready} "
      f"(期望 1/1/1/{TOTAL-1})")

ok = (
    len(eps) == TOTAL
    and parse_runs == TOTAL
    and finalize_eps == TOTAL
    and all(er.get("final_video_path") for er in eps)
    and first_frame == 1 and each_scene == 1 and end == 1 and episode_ready == TOTAL - 1
    and len(bible) > 0
)
print("\nSMOKE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
