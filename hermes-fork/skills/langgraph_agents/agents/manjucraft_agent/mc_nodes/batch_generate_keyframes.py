"""Generate a keyframe image for every shot."""

from __future__ import annotations

import asyncio
import os
import re

from mc_services.agnes_media import generate_image
from mc_state import AgentState, ShotResult, episode_project_dir


# Camera-movement hints: 中文 → English phrase appended to the generation
# prompt so the model composes/animates the shot with the intended camera work.
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
    # Collect every character angle (multi-view set), deduped, as the keyframe
    # generation identity anchor for consistency.
    ref_images: list[str] = []
    _seen = set()
    for c in characters:
        imgs = c.get("view_images") or []
        if not imgs and c.get("ref_image"):
            imgs = [c["ref_image"]]
        for v in imgs:
            if v and v not in _seen:
                _seen.add(v)
                ref_images.append(v)
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
        motion = shot.get("motion")
        if motion and motion != "固定":
            prompt = f"{prompt}, {MOTION_EN.get(motion, '')}"
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
