"""Generate short videos from keyframes."""

from __future__ import annotations

import asyncio
import os
import re

from mc_services.agnes_media import generate_video_to_file
from mc_state import AgentState, episode_project_dir


def _duration_to_frames(duration: float) -> int:
    """Pick a num_frames value that approximates duration at 24fps and satisfies 8n+1."""
    import math

    target = int(duration * 24)
    n = max(1, (target - 1) // 8)
    n = min(n, 55)  # 8*55+1 = 441
    return 8 * n + 1


# Camera-movement hints: 中文 → English phrase (see batch_generate_keyframes).
MOTION_EN = {
    "固定": "static shot, locked camera, no camera movement",
    "推进": "slow push in, camera dollies forward toward the subject",
    "后退": "slow pull out, camera dollies backward away from the subject",
    "左摇": "camera pans left across the scene",
    "右摇": "camera pans right across the scene",
    "上移": "camera tilts up",
    "下移": "camera tilts down",
    "旋转": "camera slowly orbits around the subject",
}


def _video_dims(res: str) -> tuple[int, int]:
    """Map the agent "WxH" resolution to video width/height (capped for the API)."""
    m = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", str(res))
    if not m:
        return (1024, 576)
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return (1024, 576)
    # Cap the long edge at 1280 (Agnes video model practical ceiling) while
    # preserving the chosen aspect ratio.
    long_edge = max(w, h)
    scale = min(1.0, 1280.0 / long_edge)
    vw = max(64, int(round(w * scale / 8) * 8))
    vh = max(64, int(round(h * scale / 8) * 8))
    return (vw, vh)


async def batch_generate_video(state: AgentState) -> dict:
    """Generate a short video for each shot that has a keyframe."""
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}

    project_dir = episode_project_dir(state)
    shot_results = state.get("shot_results", [])
    shots = state["shots"]
    vw, vh = _video_dims(state.get("resolution") or "1080x1920")

    async def gen_one(result: dict) -> dict:
        if result.get("status") == "error" or not result.get("keyframe_path"):
            return result
        idx = result["index"]
        shot = next((s for s in shots if s["index"] == idx), None)
        duration = shot["duration"] if shot else 5.0
        num_frames = _duration_to_frames(duration)
        out_path = os.path.join(project_dir, "videos", f"shot_{idx:03d}.mp4")
        video_prompt = shot["video_prompt"] if shot else "subtle cinematic motion"
        motion = shot.get("motion") if shot else None
        if motion and motion != "固定":
            video_prompt = f"{video_prompt}, {MOTION_EN.get(motion, '')}"
        try:
            await generate_video_to_file(
                video_prompt,
                output_path=out_path,
                image=result["keyframe_path"],
                width=vw, height=vh,
                num_frames=num_frames, frame_rate=24,
            )
            result["video_path"] = out_path
            result["status"] = "video_ok"
        except Exception as exc:
            result["status"] = "video_fail"
            result["error"] = str(exc)
        return result

    updated = await asyncio.gather(*(gen_one(r) for r in shot_results))
    return {"shot_results": updated}
