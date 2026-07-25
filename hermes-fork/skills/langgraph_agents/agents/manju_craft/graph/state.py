"""Shared state schema for the LangGraph workflow."""

from __future__ import annotations

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


class AgentState(TypedDict):
    # Inputs
    script: str
    api_key: str
    project_name: str

    # Parsed shots
    shots: list[Shot]

    # Characters
    characters: list[Character]

    # Per-shot results
    shot_results: list[ShotResult]

    # Global progress
    current_shot_index: int
    total_shots: int
    completed_shots: int
    status: str

    # Outputs
    final_video_path: NotRequired[str]
    jianying_draft_path: NotRequired[str]
    assets_zip_path: NotRequired[str]

    # Control
    stop_requested: bool
    max_retries: int
