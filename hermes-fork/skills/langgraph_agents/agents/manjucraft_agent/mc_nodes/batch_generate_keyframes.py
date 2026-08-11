"""Generate a keyframe image for every shot."""

from __future__ import annotations

import asyncio
import os
import re

from mc_services.agnes_media import generate_image
from mc_state import AgentState, ShotResult, episode_project_dir


def _size_for_resolution(res: str) -> str:
    """Map the agent's "WxH" resolution to a keyframe size string.

    Agnes image API takes a size string; we keep the long edge ~1024 and match
    the aspect ratio of the chosen resolution so the keyframe composition fits
    the final canvas without awkward letterboxing (debt #6).
    """
    m = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", str(res))
    if not m:
        return "1024x576"
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return "1024x576"
    long_edge = 1024
    if h >= w:  # portrait
        return f"576x{long_edge}"
    return f"{long_edge}x576"


async def batch_generate_keyframes(state: AgentState) -> dict:
    """Generate a keyframe image per shot; fold in steering notes (debt #5)."""
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}

    project_dir = episode_project_dir(state)
    shots = state["shots"]
    characters = state.get("characters", [])
    ref_images = [c["ref_image"] for c in characters if c.get("ref_image")]
    steer = (state.get("steer_notes") or "").strip()
    # Resolution drives the keyframe canvas size (debt #6: user-controllable).
    kf_size = _size_for_resolution(state.get("resolution") or "1080x1920")

    shot_results: list[ShotResult] = []
    for shot in shots:
        shot_results.append({
            "index": shot["index"],
            "status": "keyframe_gen",
            "retry_count": 0,
            "subtitle": shot["dialogue"],
        })

    async def gen_one(idx: int, shot: dict) -> ShotResult:
        result = shot_results[idx]
        prompt = shot["prompt"]
        if steer:
            prompt = f"{prompt}, 用户修改：{steer}"
        out_path = os.path.join(project_dir, "keyframes", f"shot_{shot['index']:03d}.png")
        try:
            await generate_image(
                prompt, size=kf_size,
                reference_images=ref_images if ref_images else None,
                output_path=out_path,
            )
            result["keyframe_path"] = out_path
            result["status"] = "keyframe_ok"
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
        return result

    updated = await asyncio.gather(*(gen_one(i, s) for i, s in enumerate(shots)))
    # Progress fields actually updated now (debt #6).
    return {
        "shot_results": updated,
        "current_shot_index": 0,
        "completed_shots": 0,
        "total_shots": len(shots),
    }
