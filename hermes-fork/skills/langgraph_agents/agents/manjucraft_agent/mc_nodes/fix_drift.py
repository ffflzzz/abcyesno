"""Regenerate keyframes that failed consistency check (debt #9: real re-score + warning)."""

from __future__ import annotations

import asyncio
import os

from mc_services.agnes_media import generate_image
from mc_nodes.consistency_check import _score_similarity
from mc_state import AgentState, ShotResult, episode_project_dir

# Configurable threshold (env override), re-scores after each retry and
# surfaces a warning instead of silently passing.
THRESHOLD = float(os.environ.get("MANJUCRAFT_DRIFT_THRESHOLD", "0.7"))


async def fix_drift(state: AgentState) -> dict:
    """Retry low-consistency keyframes, re-score, and warn if still low."""
    project_dir = episode_project_dir(state)
    shot_results = state.get("shot_results", [])
    shots = state["shots"]
    max_retries = state.get("max_retries", 3)
    characters = state.get("characters", [])
    # Collect every character angle (multi-view set), deduped, as the drift-fix
    # regeneration identity anchor for consistency.
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
    first_path = next((r.get("keyframe_path") for r in shot_results if r.get("keyframe_path")), None)

    warnings: list[str] = list(state.get("consistency_warnings") or [])

    async def fix_one(result: dict) -> dict:
        idx = result["index"]
        shot = next((s for s in shots if s["index"] == idx), None)
        if not shot:
            return result
        result.setdefault("retry_count", 0)

        while result.get("consistency_score", 1.0) < THRESHOLD and result["retry_count"] < max_retries:
            result["retry_count"] += 1
            result["status"] = "keyframe_fix"
            prompt = f"{shot['prompt']}, consistent character design, same style and lighting"
            out_path = os.path.join(project_dir, "keyframes", f"shot_{idx:03d}_fix{result['retry_count']}.png")
            try:
                await generate_image(
                    prompt, size="1024x576",
                    reference_images=ref_images if ref_images else None,
                    output_path=out_path,
                )
                result["keyframe_path"] = out_path
                # Re-score against the first keyframe (real, not faked).
                if first_path:
                    result["consistency_score"] = _score_similarity(first_path, out_path)
            except Exception as exc:
                result["error"] = str(exc)
                break

        if result.get("consistency_score", 1.0) < THRESHOLD:
            # Don't silently pass -- record a warning for the approval gate.
            result["status"] = "consistency_fail"
            warnings.append(f"shot {idx} consistency {result.get('consistency_score', 0):.2f} < {THRESHOLD}")
        else:
            result["status"] = "keyframe_ok"
        return result

    updated = await asyncio.gather(*(fix_one(r) for r in shot_results if r.get("status") != "error"))
    error_shots = [r for r in shot_results if r.get("status") == "error"]
    return {"shot_results": updated + error_shots, "consistency_warnings": warnings}
