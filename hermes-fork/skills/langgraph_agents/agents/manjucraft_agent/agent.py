#!/usr/bin/env python3
"""manjucraft_agent -- LangGraph漫剧 Agent（v2，独立新包）。

输入脚本 -> 输出竖屏漫剧成片(mp4) + 剪映草稿(json) + 素材包(zip)。
支持 single（单条）与 series（多集长剧）两种模式；series 模式下第 1 集
锁定角色圣经(character_bible)，续集复用以保证跨集一致性，并支持两级
checkpoint 续跑。

本包不复用、不修改旧 manju_craft；内部子模块统一加 mc_ 前缀避免与同进程
其它 agent 的顶层模块名（如 graph）碰撞。

环境变量的约定：
  AGNES_API_KEY / AGNES_BASE_URL / AGNES_MODEL   Agnes 凭证（运行时也会写入 env）
  MANJUCRAFT_AGENT_MOCK                          设任意非空值 -> 服务层走本地 mock（免额度）
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Any, Dict

# Make the package directory importable so ``mc_*`` submodules resolve with the
# same absolute names the runtime uses. (Same trick the old agent used, but with
# uniquely-prefixed module names to avoid cross-agent collisions -- debt #2.)
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from mc_graph import build_graph  # noqa: E402
from langgraph_agents.langgraph_runtime import _get_agnes_credentials  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

DEFAULT_PROJECT = "manjucraft-export"
DEFAULT_MAX_RETRIES = 3


def _slugify(text: str, max_len: int = 40) -> str:
    """Create a filesystem-safe slug from the input text."""
    cleaned = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    cleaned = cleaned.strip().replace(" ", "-")
    if not cleaned:
        return "untitled"
    return cleaned[:max_len].rstrip("-")


def _resolve_api_key() -> str:
    """Return the Agnes API key from env or Hermes config."""
    return _get_agnes_credentials()[1]


def build_initial_state(input_text: str, project_name: str = None, style: str = "二次元") -> Dict[str, Any]:
    """Map the runtime's free-text input to the AgentState (single mode)."""
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            "No Agnes API key found. Set AGNES_API_KEY or configure "
            "providers.custom.api_key in Hermes config.yaml."
        )
    os.environ["AGNES_API_KEY"] = api_key

    resolved_project = (project_name or "").strip() or os.environ.get("MANJUCRAFT_PROJECT", "").strip()
    if not resolved_project:
        resolved_project = f"{DEFAULT_PROJECT}-{_slugify(input_text)}-{datetime.now():%Y%m%d-%H%M%S}"

    return {
        "mode": "single",
        "series_script": "",
        "series_name": "",
        "total_episodes": 1,
        "current_episode": 0,
        "consistency_policy": "lock_bible",
        "episode_scripts": [input_text],
        "character_bible": [],
        "episode_results": [],
        "script": input_text,
        "style": style,
        "project_name": resolved_project,
        "api_key": api_key,
        "shots": [],
        "characters": [],
        "shot_results": [],
        "total_shots": 0,
        "current_shot_index": 0,
        "completed_shots": 0,
        "status": "idle",
        "stop_requested": False,
        "max_retries": DEFAULT_MAX_RETRIES,
        "steer_notes": "",
        "consistency_warnings": [],
    }


def build_initial_state_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Map a structured contract input (L2) to the AgentState.

    The runtime calls this when the frontend submits a manifest-driven form.
    ``mode`` selects single (needs ``script``) vs series (needs ``series_script``).
    """
    if not isinstance(obj, dict):
        raise RuntimeError("manjucraft_agent input must be an object")

    mode = (obj.get("mode") or "single").strip().lower() or "single"
    style = (obj.get("style") or "二次元").strip() or "二次元"
    project_name = (obj.get("project_name") or "").strip()

    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            "No Agnes API key found. Set AGNES_API_KEY or configure "
            "providers.custom.api_key in Hermes config.yaml."
        )
    os.environ["AGNES_API_KEY"] = api_key

    if mode == "series":
        series_script = (obj.get("series_script") or "").strip()
        if not series_script:
            raise RuntimeError("manjucraft_agent series mode requires a non-empty 'series_script'")
        total = max(1, int(obj.get("total_episodes", 3) or 3))
        consistency = (obj.get("consistency_policy") or "lock_bible").strip() or "lock_bible"
        series_name = (obj.get("series_name") or "").strip()
        return {
            "mode": "series",
            "series_script": series_script,
            "series_name": series_name or f"series-{_slugify(series_script, 20)}",
            "total_episodes": total,
            "current_episode": 0,
            "consistency_policy": consistency,
            "episode_scripts": [],  # plan_episodes fills this
            "character_bible": [],
            "episode_results": [],
            "script": "",
            "style": style,
            "project_name": project_name or f"manjucraft-series-{datetime.now():%Y%m%d-%H%M%S}",
            "api_key": api_key,
            "shots": [],
            "characters": [],
            "shot_results": [],
            "total_shots": 0,
            "current_shot_index": 0,
            "completed_shots": 0,
            "status": "idle",
            "stop_requested": False,
            "max_retries": DEFAULT_MAX_RETRIES,
            "steer_notes": "",
            "consistency_warnings": [],
        }

    # single mode
    script = (obj.get("script") or "").strip()
    if not script:
        raise RuntimeError("manjucraft_agent single mode requires a non-empty 'script'")
    return build_initial_state(script, project_name=project_name, style=style)


# --- Workflow stage map (contract L5) -----------------------------------
# Ordered (node name, human label) pairs used by the runtime to compute
# per-stage progress. Approval gates are included so the bar advances through
# each human-approval point.
WORKFLOW_STAGES = [
    ("plan_episodes", "规划系列"),
    ("parse_script", "解析剧本"),
    ("generate_characters", "生成角色"),
    ("gate_first_frame", "首帧确认"),
    ("batch_generate_keyframes", "生成分镜图"),
    ("consistency_check", "一致性检查"),
    ("gate_each_scene", "分镜确认"),
    ("fix_drift", "修正漂移"),
    ("batch_generate_video", "生成视频"),
    ("generate_tts", "生成配音"),
    ("merge_and_concat", "合成成片"),
    ("generate_jianying_draft", "导出剪映草稿"),
    ("gate_end", "成片确认"),
    ("finalize_episode", "本集完成"),
    ("finalize_series", "系列完成"),
]


def summarize_state(result: Dict[str, Any]) -> Dict[str, Any]:
    """Typed summary + artifacts (contract L3/L5). Series-aware."""
    if not isinstance(result, dict):
        return {"summary": "", "artifacts": []}

    status = result.get("status", "unknown")
    episodes = result.get("episode_results") or []
    parts = [f"status={status}", f"episodes={len(episodes)}/{result.get('total_episodes', 1)}"]

    artifacts: list[Dict[str, Any]] = []
    for er in episodes:
        ep = er.get("episode")
        if er.get("final_video_path"):
            artifacts.append({"id": f"ep{ep}_video", "type": "video", "source": "path", "path": er["final_video_path"], "label": f"第{ep}集成片"})
        if er.get("jianying_draft_path"):
            artifacts.append({"id": f"ep{ep}_jianying", "type": "file", "mime": "application/json", "source": "path", "path": er["jianying_draft_path"], "label": f"第{ep}集剪映草稿"})
        if er.get("assets_zip_path"):
            artifacts.append({"id": f"ep{ep}_assets", "type": "file", "mime": "application/zip", "source": "path", "path": er["assets_zip_path"], "label": f"第{ep}集素材包"})

    # Fall back to single-episode fields when no episode_results recorded.
    if not artifacts:
        final = result.get("final_video_path", "")
        draft = result.get("jianying_draft_path", "")
        assets = result.get("assets_zip_path", "")
        completed = result.get("completed_shots", 0)
        total = result.get("total_shots", 0)
        parts.append(f"shots={completed}/{total}")
        if final:
            artifacts.append({"id": "video", "type": "video", "source": "path", "path": final, "label": "成片"})
        if draft:
            artifacts.append({"id": "jianying", "type": "file", "mime": "application/json", "source": "path", "path": draft, "label": "剪映草稿"})
        if assets:
            artifacts.append({"id": "assets", "type": "file", "mime": "application/zip", "source": "path", "path": assets, "label": "素材包"})

    return {"summary": "; ".join(parts), "artifacts": artifacts}


# Compile with a checkpointer so LangGraph interrupt() / human-in-the-loop
# approval gates work, AND so per-episode / cross-episode resume works via a
# single thread_id (the self-looping graph reuses the same checkpoint).
graph = build_graph().compile(checkpointer=MemorySaver())
