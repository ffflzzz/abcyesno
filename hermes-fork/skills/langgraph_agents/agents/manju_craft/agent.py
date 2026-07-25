#!/usr/bin/env python3
"""ManjuCraft agent -- wraps the manju-craft video-generation workflow.

The agent exposes ``build_graph()`` and ``build_initial_state()`` so the
LangGraph runtime can drive the workflow from a free-text prompt.

Environment variables
---------------------
AGNES_API_KEY / AGNES_BASE_URL / AGNES_MODEL
    Credentials for the Agnes AI endpoint. When absent, the agent falls back
    to the active Hermes ``config.yaml`` (``providers.custom`` or
    ``delegation`` blocks).
MANJU_CRAFT_PROJECT
    Override the generated project name.
MANJU_CRAFT_MOCK
    When set (any non-empty value), replace media generation with local
    smoke-test stubs. This lets the full graph run without consuming image /
    video / TTS credits.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Make the local ``graph`` package importable with the same absolute names used
# by the original manju-craft project.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

# ---------------------------------------------------------------------------
# Smoke-test stubs (credit-free verification)
# ---------------------------------------------------------------------------


def _ffmpeg() -> str:
    """Return a working ffmpeg executable or raise."""
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg.exe")
    if os.path.exists(bundled):
        return bundled
    system = subprocess.run(["where", "ffmpeg"], capture_output=True, text=True)
    if system.returncode == 0 and system.stdout.strip():
        return system.stdout.strip().splitlines()[0]
    raise RuntimeError("ffmpeg not found; smoke mode requires ffmpeg")


def _make_gray_png(path: str) -> str:
    """Create a minimal 1024x576 gray PNG."""
    from PIL import Image

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1024, 576), color=(128, 128, 128)).save(path)
    return path


def _make_test_video(output_path: str, duration: float = 5.0) -> str:
    """Create a solid-gray test MP4 with ffmpeg."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s=1024x576:d={duration}",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            output_path,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return output_path


def _make_silent_audio(output_path: str, duration: float = 5.0) -> str:
    """Create a silent MP3 of the requested duration with ffmpeg."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            str(duration),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            output_path,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return output_path


def _apply_smoke_mocks() -> None:
    """Patch media services so the graph can be exercised without credits."""
    from graph.services import agnes_media, llm, tts

    async def mock_parse_script_to_shots(script: str, *, model: str | None = None) -> list[dict]:
        return [
            {
                "index": 0,
                "description": "Smoke test scene",
                "dialogue": "这是一段烟测试旁白。",
                "duration": 3.0,
                "prompt": "A gray placeholder scene for smoke testing, cinematic",
                "video_prompt": "Subtle camera movement on a gray placeholder scene",
            }
        ]

    async def mock_generate_image(
        prompt: str,
        size: str = "1024x768",
        *,
        reference_images: list[str] | None = None,
        output_path: str | None = None,
        response_format: str = "b64_json",
        timeout: float = 180.0,
    ) -> str:
        assert output_path is not None
        return _make_gray_png(output_path)

    async def mock_generate_video_to_file(
        prompt: str,
        output_path: str,
        *,
        image: str | None = None,
        keyframes: list[str] | None = None,
        width: int = 1152,
        height: int = 768,
        num_frames: int = 121,
        frame_rate: int = 24,
        max_attempts: int = 3,
    ) -> str:
        # Approximate duration from frame count / frame_rate.
        duration = max(1.0, num_frames / max(1, frame_rate))
        return _make_test_video(output_path, duration=duration)

    async def mock_generate_tts(
        text: str,
        output_path: str,
        *,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
    ) -> str:
        return _make_silent_audio(output_path, duration=3.0)

    def mock_adjust_audio_duration(
        input_path: str, output_path: str, target_seconds: float
    ) -> str:
        # The silent file already has the intended duration; just copy it.
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(Path(input_path).read_bytes())
        return output_path

    llm.parse_script_to_shots = mock_parse_script_to_shots
    agnes_media.generate_image = mock_generate_image
    agnes_media.generate_video_to_file = mock_generate_video_to_file
    tts.generate_tts = mock_generate_tts
    tts.adjust_audio_duration = mock_adjust_audio_duration


# Apply mocks *before* importing the graph so that every node module picks up
# the patched service functions when it is first loaded.
if os.environ.get("MANJU_CRAFT_MOCK"):
    _apply_smoke_mocks()

from graph.graph import build_graph  # noqa: E402
from langgraph_agents.langgraph_runtime import _get_agnes_credentials  # noqa: E402


DEFAULT_PROJECT = "manju-craft-export"
DEFAULT_MAX_RETRIES = 3


def _slugify(text: str, max_len: int = 40) -> str:
    """Create a filesystem-safe slug from the input script."""
    cleaned = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    cleaned = cleaned.strip().replace(" ", "-")
    if not cleaned:
        return "untitled"
    return cleaned[:max_len].rstrip("-")


def _resolve_api_key() -> str:
    """Return the Agnes API key from env or Hermes config."""
    return _get_agnes_credentials()[1]


def build_initial_state(input_text: str, project_name: str = None) -> Dict[str, Any]:
    """Map the runtime's free-text input to the manju-craft AgentState."""
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            "No Agnes API key found. Set AGNES_API_KEY or configure "
            "providers.custom.api_key in Hermes config.yaml."
        )

    # Ensure downstream services see the key.
    os.environ["AGNES_API_KEY"] = api_key

    resolved_project = (project_name or "").strip() or os.environ.get("MANJU_CRAFT_PROJECT", "").strip()
    if not resolved_project:
        resolved_project = f"{DEFAULT_PROJECT}-{_slugify(input_text)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    return {
        "script": input_text,
        "api_key": api_key,
        "project_name": resolved_project,
        "shots": [],
        "characters": [],
        "shot_results": [],
        "current_shot_index": 0,
        "total_shots": 0,
        "completed_shots": 0,
        "status": "idle",
        "stop_requested": False,
        "max_retries": DEFAULT_MAX_RETRIES,
    }


def build_initial_state_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Map a structured contract input (L2) to the manju-craft AgentState.

    The runtime calls this when the frontend submits a manifest-driven form.
    ``script`` is required; ``style`` folds into the script as a style hint;
    ``project_name`` is optional.
    """
    if not isinstance(obj, dict):
        raise RuntimeError("manju_craft input must be an object with a 'script' field")
    script = (obj.get("script") or "").strip()
    if not script:
        raise RuntimeError("manju_craft input requires a non-empty 'script'")
    style = (obj.get("style") or "二次元").strip()
    project_name = (obj.get("project_name") or "").strip()
    text = f"[风格：{style}]\n{script}" if style else script
    return build_initial_state(text, project_name=project_name)


# --- Workflow stage map (contract L5) -----------------------------------
# Ordered (node name, human label) pairs used by the runtime to compute
# per-stage progress percentages. Approval gates are included so the
# progress bar advances through each human-approval point.
WORKFLOW_STAGES = [
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
    ("finalize", "完成"),
]


# Compile with a checkpointer so LangGraph interrupt() / human-in-the-loop
# approval gates work. MemorySaver keeps checkpoints in-process (the Hermes
# runtime process), keyed by thread_id, which is exactly what HITL needs.
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

graph = build_graph().compile(checkpointer=MemorySaver())
