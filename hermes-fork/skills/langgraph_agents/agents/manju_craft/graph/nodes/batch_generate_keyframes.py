"""Generate keyframes for every shot."""

from __future__ import annotations

import asyncio
import os

from graph.services.agnes_media import generate_image
from graph.state import AgentState, ShotResult


async def batch_generate_keyframes(state: AgentState) -> dict:
    """Generate a keyframe image for each shot."""
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}
    project_dir = _project_dir(state)
    shots = state["shots"]
    characters = state.get("characters", [])
    ref_images = [c["ref_image"] for c in characters if c.get("ref_image")]

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
        out_path = os.path.join(project_dir, "keyframes", f"shot_{shot['index']:03d}.png")
        try:
            await generate_image(
                prompt,
                size="1024x576",
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
    return {"shot_results": updated, "current_shot_index": 0}


def _project_dir(state: AgentState) -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".manjucraft", "projects", state["project_name"])
