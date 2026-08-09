"""Finalize one episode and advance the series loop."""

from __future__ import annotations

from mc_state import AgentState, EpisodeResult


async def finalize_episode(state: AgentState) -> dict:
    """Record this episode's outputs, advance current_episode, reset per-episode fields."""
    ep = int(state.get("current_episode", 0) or 0)
    results: list[EpisodeResult] = list(state.get("episode_results") or [])
    results.append({
        "episode": ep,
        "status": state.get("status", "done"),
        "final_video_path": state.get("final_video_path", ""),
        "jianying_draft_path": state.get("jianying_draft_path", ""),
        "assets_zip_path": state.get("assets_zip_path", ""),
    })
    return {
        "episode_results": results,
        "current_episode": ep + 1,
        # Reset per-episode channels so the next loop iteration starts clean.
        "shots": [],
        "characters": [],
        "shot_results": [],
        "total_shots": 0,
        "current_shot_index": 0,
        "completed_shots": 0,
        "final_video_path": "",
        "jianying_draft_path": "",
        "assets_zip_path": "",
        "steer_notes": "",
        "consistency_warnings": [],
        "status": "episode_done",
    }
