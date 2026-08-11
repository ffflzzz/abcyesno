"""Shared state schema + output-path helpers for manjucraft_agent.

Single combined ``AgentState`` (per-episode fields + series fields) so the
existing ``langgraph_runtime`` (which drives ONE graph via ``astream`` and
snapshots THAT graph's state for HITL ``workflow.approval`` artifacts) works
without modification. The "series orchestrator" is realised as a conditional
loop in ``mc_graph`` rather than a nested subgraph -- functionally identical to
the two-layer design in the spec but runtime-compatible (single checkpoint
covers both per-episode resume and cross-episode resume).
"""

from __future__ import annotations

import os
from enum import Enum
from typing import NotRequired, TypedDict


class ShotStatus(str, Enum):
    PENDING = "pending"
    KEYFRAME_GEN = "keyframe_gen"
    KEYFRAME_OK = "keyframe_ok"
    KEYFRAME_FIX = "keyframe_fix"
    VIDEO_GEN = "video_gen"
    VIDEO_OK = "video_ok"
    VIDEO_FAIL = "video_fail"
    CONSISTENCY_CHECK = "consistency_check"
    CONSISTENCY_PASS = "consistency_pass"
    CONSISTENCY_FAIL = "consistency_fail"
    DONE = "done"


class Shot(TypedDict):
    index: int
    description: str
    dialogue: str
    duration: float
    prompt: str
    video_prompt: str


class Character(TypedDict):
    name: str
    ref_image: str
    prompt: str


class ShotResult(TypedDict):
    index: int
    keyframe_path: NotRequired[str]
    keyframe_url: NotRequired[str]
    video_path: NotRequired[str]
    video_url: NotRequired[str]
    tts_audio_path: NotRequired[str]
    tts_audio_url: NotRequired[str]
    subtitle: NotRequired[str]
    status: str
    retry_count: int
    consistency_score: NotRequired[float]
    error: NotRequired[str]
    shot_script: NotRequired[str]


class EpisodeResult(TypedDict):
    episode: int
    status: str
    final_video_path: NotRequired[str]
    jianying_draft_path: NotRequired[str]
    assets_zip_path: NotRequired[str]


class AgentState(TypedDict):
    # --- mode / series orchestration ---
    mode: str                       # 'single' | 'series' (default single)
    series_script: str
    series_name: str
    total_episodes: int
    current_episode: int
    consistency_policy: str         # 'lock_bible' (default) | 'per_episode'
    episode_scripts: list[str]      # plan_episodes fills this for series
    character_bible: list[Character]  # locked after episode 0 first-frame approve
    episode_results: list[EpisodeResult]

    # --- per-episode inputs ---
    script: str                     # = episode_scripts[current_episode]
    style: str
    project_name: str
    api_key: str

    # --- rendering params (debt #6: consumed by gen/draft nodes) ---
    resolution: str                 # "WxH" string, e.g. "1080x1920" (portrait default)
    sec_per_shot: float             # default per-shot duration override (0 = use LLM per-shot)

    # --- fixed characters (debt: user-supplied role specs for consistency) ---
    fixed_characters: NotRequired[list[Character]]  # injected verbatim; bypasses LLM char parse

    # --- parsed shots / characters ---
    shots: list[Shot]
    characters: list[Character]
    shot_results: list[ShotResult]

    # --- per-episode progress (declared + actually updated, debt #6) ---
    total_shots: int
    current_shot_index: int
    completed_shots: int

    # --- per-episode outputs ---
    final_video_path: NotRequired[str]
    jianying_draft_path: NotRequired[str]
    assets_zip_path: NotRequired[str]

    # --- control / HITL ---
    steer_notes: NotRequired[str]           # consumed by generation nodes (debt #5)
    consistency_warnings: NotRequired[list[str]]  # surfaced when drift stays low (debt #9)
    series_playlist_path: NotRequired[str]  # written by finalize_series
    status: str                             # logging only, never drives control flow (debt #10)
    stop_requested: bool
    max_retries: int


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------


def _hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.join(
        os.path.expanduser("~"), ".hermes_portable_data"
    )


def project_root(state: AgentState) -> str:
    """Base directory for a run, under HERMES_HOME (portable-data isolated)."""
    name = (state.get("project_name") or "manjucraft").strip() or "manjucraft"
    return os.path.join(_hermes_home(), "manjucraft_agent", "projects", name)


def episode_project_dir(state: AgentState) -> str:
    """Per-episode output directory (isolates episodes in a series)."""
    ep = state.get("current_episode", 0)
    return os.path.join(project_root(state), f"ep{ep:03d}")
