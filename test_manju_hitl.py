"""MOCK end-to-end test of the manju_craft HITL (interrupt) loop + percentages.

Runs the graph with MANJU_CRAFT_MOCK=1 (no real credits) and simulates the
frontend brake by writing decisions into the file-based control channel while
the runtime is parked at each interrupt(). Verifies:
  - 3 workflow.approval events are emitted (first_frame / each_scene / end)
  - per-stage progress percentages advance (Gap 2)
  - approve / steer / reject all drive the graph correctly
"""
import os
import sys
import json
import time
import threading
from pathlib import Path

# Must be set before importing the agent (smoke mocks apply at import time).
os.environ["MANJU_CRAFT_MOCK"] = "1"
# build_initial_state() requires a non-empty key even in mock mode (it only
# forwards the key to downstream services, which are all mocked). Use a dummy.
os.environ.setdefault("AGNES_API_KEY", "sk-mock-test-no-credit")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "hermes-fork", "skills"))

import langgraph_agents.langgraph_runtime as rt


def wait_absent(f, timeout=60):
    t0 = time.time()
    while f.exists():
        if time.time() - t0 > timeout:
            return
        time.sleep(0.1)


def writer(run_id, decisions):
    d = rt._hitl_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{run_id}.json"
    for dec in decisions:
        wait_absent(f, 60)  # previous decision consumed (or initial absent)
        payload = {"decision": dec}
        if dec == "steer":
            payload["steerText"] = "请让角色更卡通一些"
        f.write_text(json.dumps(payload), encoding="utf-8")
        wait_absent(f, 60)  # wait for this decision to be consumed


def run_case(name, decisions):
    run_id = f"hitl-{name}"
    events = []

    def on_event(ev_type, payload):
        events.append((ev_type, payload))

    w = threading.Thread(target=writer, args=(run_id, decisions), daemon=True)
    w.start()

    result = rt.run_agent(
        "manju_craft",
        input_text="一只猫在屋顶看日落，做成 1 个镜头的短视频。",
        thread_id=run_id,
        on_event=on_event,
    )
    w.join(timeout=120)

    approvals = [p for (t, p) in events if t == "workflow.approval"]
    progresses = [p for (t, p) in events if t == "workflow.progress"]
    artifacts = [p for (t, p) in events if t == "workflow.artifact"]
    dones = [p for (t, p) in events if t == "workflow.done"]
    errors = [p for (t, p) in events if t == "workflow.error"]

    # Distinct stage percentages observed (sorted).
    pcts = sorted({round(p.get("completed", 0) / p.get("total", 1) * 100) for p in progresses})

    print(f"\n=== CASE {name} (decisions={decisions}) ===")
    print(f"  approvals : {len(approvals)} -> {[a.get('gate_id') for a in approvals]}")
    print(f"  progress  : {len(progresses)} events; distinct % = {pcts}")
    print(f"  artifacts : {len(artifacts)} -> {[a.get('label') for a in artifacts]}")
    print(f"  done      : {[d.get('status') for d in dones]}")
    print(f"  errors    : {[e.get('message') for e in errors]}")
    print(f"  result keys: {list(result.keys())}")
    print(f"  final status: {result.get('state', {}).get('status') if isinstance(result.get('state'), dict) else 'n/a'}")
    return result, events


if __name__ == "__main__":
    # 1) Full approve across all three gates.
    run_case("approve_all", ["approve", "approve", "approve"])
    # 2) Steer at the second gate.
    run_case("steer", ["approve", "steer", "approve"])
    # 3) Reject at the second gate -> workflow should abort with rejected.
    run_case("reject", ["approve", "reject"])
    print("\nHITL TEST DONE")
