#!/usr/bin/env python3
"""One-shot driver for the manjucraft_agent LangGraph agent (v2).

Loads the bundled ``manjucraft_agent`` package, drives its compiled
self-looping graph end-to-end in single mode, auto-approves every
human-in-the-loop approval gate (first_frame / each_scene / end), and prints
a JSON summary plus the typed artifact list (video / jianying draft / assets
zip / per-shot frames & clips) so the generated results can be inspected.

Real run (default): consumes Agnes image/video credits + Edge-TTS + ffmpeg.
Export HTTPS_PROXY/HTTP_PROXY so httpx / aiohttp reach the Agnes endpoint.

Usage:
    # Single episode
    python scripts/run_manju_once.py --script "小猫在窗边看雨" --style 二次元

    # Series (multi-episode long-form); first episode locks the character
    # bible, later episodes reuse it via a lightweight episode_ready gate.
    python scripts/run_manju_once.py --mode series \
        --series-script "整部大纲：第一集…；第二集…；第三集…" \
        --total-episodes 3 --style 二次元
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "hermes-fork" / "skills"
sys.path.insert(0, str(SKILLS))

# Ensure HERMES_HOME points at the portable data dir BEFORE importing the
# runtime, so its config-path resolver (hermes_constants.get_config_path) lands
# on the portable config.yaml (which carries the Agnes key). Fall back to a
# direct read of that file if the resolver still misses.
def _resolve_portable_home():
    """Find the HERMES_HOME that actually holds config.yaml.

    In this isolated venv the shell may pass a bogus HERMES_HOME (e.g. an
    unexpanded ``$USERPROFILE``), so probe candidates and fall back to the
    portable-data default under the real user home.
    """
    env = os.environ.get("HERMES_HOME")
    home_cfg = os.path.expanduser("~/.hermes_portable_data")
    cands = [env, home_cfg] if env else [home_cfg]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "config.yaml")):
            return c
    return cands[-1]


_PORTABLE_HOME = _resolve_portable_home()
os.environ["HERMES_HOME"] = _PORTABLE_HOME

import langgraph_agents.langgraph_runtime as rt  # noqa: E402
from langgraph.types import Command  # noqa: E402

# Defensive: the runtime's own config resolver depends on the host module
# ``hermes_constants`` (absent in this isolated venv), so read the portable
# config.yaml directly and inject the Agnes key/base_url into the environment.
import yaml  # noqa: E402

_cfg_path = Path(_PORTABLE_HOME) / "config.yaml"
if _cfg_path.exists():
    try:
        _cfg = yaml.safe_load(_cfg_path.read_text(encoding="utf-8")) or {}
        _custom = (_cfg.get("providers", {}) or {}).get("custom", {}) or {}
        _deleg = _cfg.get("delegation", {}) or {}
        _k = _custom.get("api_key") or _deleg.get("api_key") or ""
        if _k and not os.environ.get("AGNES_API_KEY"):
            os.environ["AGNES_API_KEY"] = _k
        _b = _custom.get("api") or _deleg.get("base_url") or ""
        if _b and not os.environ.get("AGNES_BASE_URL"):
            os.environ["AGNES_BASE_URL"] = _b
    except Exception:
        pass


def load():
    mod = rt._load_agent_module("manjucraft_agent")
    graph = rt._get_agent_graph(mod)
    return mod, graph


async def drive(mod, graph, input_obj, thread_id):
    """Stream the graph, auto-resuming every interrupt() gate with approve."""
    config = {"configurable": {"thread_id": thread_id}}
    state = mod.build_initial_state_obj(input_obj)
    pending = state
    events: list[str] = []
    gates: list[str] = []
    while True:
        interrupted = None
        try:
            async for chunk in graph.astream(pending, config=config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                if "__interrupt__" in chunk:
                    interrupted = chunk["__interrupt__"]
                    break
                for node in chunk:
                    if node in ("__metadata__",):
                        continue
                    events.append(node)
        except Exception as exc:  # surface node failures cleanly
            return None, events, gates, f"{type(exc).__name__}: {exc}"
        if not interrupted:
            break
        intr = interrupted[0]
        val = intr.value if hasattr(intr, "value") else intr
        gate = val.get("gate_id") if isinstance(val, dict) else None
        gates.append(gate)
        events.append(f"auto-approve:{gate}")
        pending = Command(resume={"decision": "approve"})
    snap = graph.get_state(config)
    final = snap.values if snap else None
    return final, events, gates, None


def main():
    ap = argparse.ArgumentParser(description="Run manjucraft_agent (single or series mode).")
    ap.add_argument("--mode", default="single", choices=["single", "series"],
                    help="single (one-shot) or series (multi-episode long-form)")
    ap.add_argument("--script", default="", help="single-mode script text (required in single mode)")
    ap.add_argument("--series-script", default="", help="whole-series outline (required in series mode)")
    ap.add_argument("--total-episodes", type=int, default=3, help="series episode count (default 3)")
    ap.add_argument("--series-name", default="", help="series folder name override")
    ap.add_argument("--style", default="二次元", choices=["写实", "二次元", "3D"])
    ap.add_argument("--project-name", default="", help="output project folder name")
    ap.add_argument("--thread-id", default="", help="LangGraph thread id (resume key)")
    ap.add_argument("--log", default="", help="optional path to tee the run log to")
    args = ap.parse_args()

    thread_id = args.thread_id or f"manju-run-{os.getpid()}-mode-{args.mode}"

    if args.mode == "series":
        if not args.series_script.strip():
            print(json.dumps({"ok": False, "error": "series mode requires --series-script"},
                             ensure_ascii=False, indent=2))
            sys.exit(2)
        input_obj = {
            "mode": "series",
            "series_script": args.series_script,
            "total_episodes": args.total_episodes,
            "series_name": args.series_name,
            "style": args.style,
            "project_name": args.project_name,
            "consistency_policy": "lock_bible",
        }
    else:
        if not args.script.strip():
            print(json.dumps({"ok": False, "error": "single mode requires --script"},
                             ensure_ascii=False, indent=2))
            sys.exit(2)
        input_obj = {
            "script": args.script,
            "style": args.style,
            "mode": "single",
            "project_name": args.project_name,
        }

    mod, graph = load()
    final, events, gates, err = asyncio.run(drive(mod, graph, input_obj, thread_id))

    if err:
        print(json.dumps({"ok": False, "error": err, "events": events}, ensure_ascii=False, indent=2))
        sys.exit(1)

    summary = mod.summarize_state(final or {})
    out = {
        "ok": True,
        "agent": "manjucraft_agent",
        "mode": args.mode,
        "thread_id": thread_id,
        "project_name": (final or {}).get("project_name"),
        "events": events,
        "gates_auto_approved": gates,
        "summary": summary,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.log:
        try:
            Path(args.log).write_text(text, encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    main()
