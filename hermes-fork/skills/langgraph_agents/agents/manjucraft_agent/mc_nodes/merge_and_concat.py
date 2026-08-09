"""Merge generated shot videos and audios into the final MP4."""

from __future__ import annotations

import os

from mc_services.ffmpeg import merge_shots
from mc_state import AgentState, episode_project_dir


async def merge_and_concat(state: AgentState) -> dict:
    """Concatenate all shot videos and audios into a single MP4."""
    project_dir = episode_project_dir(state)
    shot_results = state.get("shot_results", [])

    video_paths = []
    audio_paths = []
    for result in shot_results:
        if result.get("video_path"):
            video_paths.append(result["video_path"])
            audio_paths.append(result.get("tts_audio_path"))

    if not video_paths:
        raise RuntimeError("no videos to merge")

    final_path = os.path.join(project_dir, "final.mp4")
    merge_shots(video_paths, audio_paths, final_path)
    return {"final_video_path": final_path, "status": "merging"}
