"""Generate short videos from keyframes."""

from __future__ import annotations

import asyncio
import os

from graph.services.agnes_media import generate_video_to_file
from graph.state import AgentState


async def batch_generate_video(state: AgentState) -> dict:
    """Generate a 5-second video for each shot that has a keyframe."""
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}
    project_dir = _project_dir(state)
    shot_results = state.get("shot_results", [])
    shots = state["shots"]

    async def gen_one(result: dict) -> dict:
        if result.get("status") == "error" or not result.get("keyframe_path"):
            return result

        idx = result["index"]
        shot = next((s for s in shots if s["index"] == idx), None)
        duration = shot["duration"] if shot else 5.0
        # num_frames must satisfy 8n+1 and frame_rate 24 gives ~5s for 121 frames.
        num_frames = _duration_to_frames(duration)
        out_path = os.path.join(project_dir, "videos", f"shot_{idx:03d}.mp4")

        try:
            await generate_video_to_file(
                shot["video_prompt"] if shot else "subtle cinematic motion",
                output_path=out_path,
                image=result["keyframe_path"],
                width=1024,
                height=576,
                num_frames=num_frames,
                frame_rate=24,
            )
            result["video_path"] = out_path
            result["status"] = "video_ok"
        except Exception as exc:
            import sys
            print(f"[batch_generate_video] shot {idx} failed: {exc}", file=sys.stderr)
            result["status"] = "video_fail"
            result["error"] = str(exc)
        return result

    updated = await asyncio.gather(*(gen_one(r) for r in shot_results))
    return {"shot_results": updated}


def _duration_to_frames(duration: float) -> int:
    """Pick a num_frames value that approximates duration at 24fps and satisfies 8n+1."""
    import math

    target = int(duration * 24)
    # Find nearest value <= target satisfying 8n+1
    n = max(1, (target - 1) // 8)
    # Also ensure <= 441
    n = min(n, 55)  # 8*55+1 = 441
    return 8 * n + 1


def _project_dir(state: AgentState) -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".manjucraft", "projects", state["project_name"])
