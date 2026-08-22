#!/usr/bin/env python3
"""单集真实跑（unset mock）：真调 Agnes 图片/视频生成，验证配额回退与产物。

用法:
  hermes-fork/.venv/Scripts/python.exe scripts/run_episode_real.py
"""
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILLS = os.path.join(REPO, "hermes-fork", "skills")

# 关键：不设置 MANJUCRAFT_AGENT_MOCK -> 走真实 Agnes API
os.environ.setdefault("AGNES_API_KEY", os.environ.get("AGNES_API_KEY") or "")
os.environ.setdefault("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes_portable_data"))

sys.path.insert(0, SKILLS)
sys.path.insert(0, os.path.join(REPO, "hermes-fork"))
sys.path.insert(0, os.path.join(REPO, "hermes-fork", "skills", "langgraph_agents", "agents", "manjucraft_agent"))

if not os.environ.get("AGNES_API_KEY"):
    print("ERROR: AGNES_API_KEY 未设置（需真实 key 才能跑）")
    sys.exit(1)
if not os.environ.get("AGNES_FALLBACK_API_KEY"):
    print("WARN: AGNES_FALLBACK_API_KEY 未设置，超额后将无法回退（仅主 key 跑）")

from mc_services import agnes_media as am
from langgraph_agents.langgraph_runtime import run_agent, discover_agents  # noqa: E402

print("=== discover_agents ===", discover_agents())
print("fallback_key:", (am._fallback_api_key() or "NONE")[:12] + "...")
print("quota_remaining(before):", am._token_plan_quota_remaining())

# 单集小剧本：3 个镜头足够验证（约 9 秒视频额度）
series_script = (
    "第一集：少女小满在雨后的天台发现一只发光的橘猫，"
    "猫眨眨眼化作一缕光钻进她的项链。小满握紧项链，"
    "远处传来古老的钟声，故事就此开始。"
)

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

t0 = time.time()
result = run_agent(
    "manjucraft_agent",
    input_obj={
        "mode": "series",
        "series_script": series_script,
        "total_episodes": 1,
        "style": "二次元",
        "consistency_policy": "lock_bible",
        "resolution": "1080x1920",
        "sec_per_shot": 3,
    },
    auto_approve=True,
    on_event=on_event,
    run_id="wf-real-ep1",
)
dt = time.time() - t0

print(f"\n=== RESULT (elapsed {dt:.1f}s) ===")
if result.get("error"):
    print("ERROR:", result["error"])
    sys.exit(1)
print("summary:", result.get("output", "")[:300])
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

print("\nquota_remaining(after):", am._token_plan_quota_remaining())
print("on_fallback:", am.video_on_fallback())
print("usage_file:", am._VIDEO_USAGE_FILE)

# 打印产物目录
for er in eps:
    proj = er.get("project_dir") or er.get("episode_dir")
    if proj:
        print("\nPROJECT_DIR:", proj)

print("\nREAL_RUN:", "DONE" if eps else "FAIL")
sys.exit(0 if eps else 1)
