#!/usr/bin/env python3
"""manjucraft_agent series 真实冒烟测试（真实 Agnes API，会烧额度）。

用法:
  hermes-fork/.venv/Scripts/python.exe scripts/smoke_manjucraft_real.py [集数]

从 HERMES_HOME/.env 读 AGNES_API_KEY（与 Electron 主进程 hermes-runner 同源），
真实调用 Agnes 生图/生视频/TTS，auto_approve 自动通过审批门。
"""
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILLS = os.path.join(REPO, "hermes-fork", "skills")
# 便携版 HERMES_HOME。环境里可能残留系统 Hermes 的 HERMES_HOME（AppData/Local/hermes），
# 必须强制覆盖，否则 _get_agnes_credentials 会读错 config.yaml、找不到 key。
HERMES_HOME = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), ".hermes_portable_data")
os.environ["HERMES_HOME"] = HERMES_HOME

# 从 .env 读真实 key（不打印值）
if not os.environ.get("AGNES_API_KEY"):
    env_file = os.path.join(HERMES_HOME, ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if line.startswith("AGNES_API_KEY="):
                os.environ["AGNES_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
os.environ.setdefault("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")

sys.path.insert(0, SKILLS)
sys.path.insert(0, os.path.join(REPO, "hermes-fork"))

from langgraph_agents.langgraph_runtime import run_agent, discover_agents  # noqa: E402

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 2

print(f"=== discover_agents ===", discover_agents())
print(f"=== 真实模式 series {TOTAL} 集 (MOCK 未设) ===", flush=True)

events = []
trace = []
t0 = time.time()

def on_event(etype, payload):
    events.append((etype, payload))
    if etype == "workflow.trace":
        trace.append((payload.get("node"), payload.get("status"), payload.get("episode")))
    elif etype == "workflow.progress":
        print(f"[{time.time()-t0:6.1f}s] progress {payload.get('stage') or payload.get('step_id')} "
              f"{payload.get('completed')}/{payload.get('total')}", flush=True)
    elif etype == "workflow.artifact":
        print(f"[{time.time()-t0:6.1f}s] artifact {payload.get('label')} -> {payload.get('path','')}", flush=True)
    elif etype == "workflow.approval":
        print(f"[{time.time()-t0:6.1f}s] approval gate={payload.get('gate_id')} (auto-approve)", flush=True)
    elif etype in ("workflow.started", "workflow.graph", "workflow.done", "workflow.error"):
        print(f"[{time.time()-t0:6.1f}s] {etype} {json.dumps(payload, ensure_ascii=False)[:160]}", flush=True)

series_script = (
    "第一集：一只小黑猫在夜晚的巷子里发现了一颗会发光的种子。"
    "第二集：黑猫把种子种进花盆，第二天长出了一棵会说话的树。"
)

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
    run_id="wf-real-series",
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

parse_runs = sum(1 for n, s, _ in trace if n == "parse_script" and s == "running")
approvals = [p.get("gate_id") for t, p in events if t == "workflow.approval"]
print(f"循环校验: parse_script={parse_runs} (期望 {TOTAL}), "
      f"审批门 first_frame={approvals.count('first_frame')} episode_ready={approvals.count('episode_ready')}")

ok = len(eps) == TOTAL and parse_runs == TOTAL and all(er.get("final_video_path") for er in eps)
print("\nREAL SMOKE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
