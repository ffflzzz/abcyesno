"""Merge generated shot videos and audios into the final MP4."""

from __future__ import annotations

import os

from mc_services.ffmpeg import merge_shots
from mc_state import AgentState, episode_project_dir


async def merge_and_concat(state: AgentState) -> dict:
    """Concatenate all shot videos and audios into a single MP4.

    Soft-fail semantics: when no shot has a video (e.g. every shot was marked
    ``status="error"`` upstream by fix_drift / consistency_check and
    silently skipped by batch_generate_video) we record the failure in
    state instead of raising. The runtime sees ``status="merge_failed"`` at
    normal completion, emits a ``workflow.error`` event with the count, and
    the frontend shows a run-error banner. The downstream generate_jianying_draft
    can still emit a usable draft (referencing the empty final_video_path).
    """
    project_dir = episode_project_dir(state)
    shot_results = state.get("shot_results", [])

    video_paths = []
    audio_paths = []
    skipped: list[int] = []
    for result in shot_results:
        vp = result.get("video_path")
        if vp:
            video_paths.append(vp)
            audio_paths.append(result.get("tts_audio_path"))
        else:
            skipped.append(result.get("index", -1))

    if not video_paths:
        total = len(shot_results)
        idx_list = ",".join(str(i) for i in skipped[:8]) if skipped else "(none)"
        more = "" if len(skipped) <= 8 else f" (+{len(skipped) - 8} more)"
        return {
            "final_video_path": None,
            "status": "merge_failed",
            "merge_error": (
                f"全部 {total} 个 shot 都缺少视频，无法合成。缺失 shot index: [{idx_list}{more}]。"
                "通常是因为上游 keyframe 生成失败后 batch_generate_video/fix_drift 静默跳过。"
                "请回到分镜 phase 单独重生成出问题的镜。"
            ),
            "missing_videos": len(skipped),
        }

    final_path = os.path.join(project_dir, "final.mp4")
    try:
        merge_shots(video_paths, audio_paths, final_path)
    except Exception as exc:
        return {
            "final_video_path": None,
            "status": "merge_failed",
            "merge_error": f"ffmpeg merge failed for {len(video_paths)} videos: {exc}",
        }
    out: dict = {"final_video_path": final_path, "status": "merging"}
    if skipped:
        out["merge_warning"] = f"concat 含 {len(skipped)} 个空视频位的 shot（index {skipped[:8]}{'+…' if len(skipped) > 8 else ''}），成片会有黑段"
    return out
