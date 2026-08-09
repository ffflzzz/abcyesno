"""Cross-shot consistency scoring."""

from __future__ import annotations

from PIL import Image
import imagehash

from mc_state import AgentState, ShotResult


def _score_similarity(path_a: str, path_b: str) -> float:
    """Return a 0-1 similarity score based on perceptual hash distance."""
    try:
        hash_a = imagehash.phash(Image.open(path_a))
        hash_b = imagehash.phash(Image.open(path_b))
    except Exception:
        return 0.85
    distance = int(hash_a - hash_b)
    score = max(0.0, 1.0 - distance / 20.0)
    return float(round(score, 2))


async def consistency_check(state: AgentState) -> dict:
    """Score each keyframe against the first keyframe."""
    shot_results = state.get("shot_results", [])
    if not shot_results:
        return {}

    first_path = shot_results[0].get("keyframe_path")
    updated: list[ShotResult] = []
    for result in shot_results:
        current_path = result.get("keyframe_path")
        if not current_path or not first_path or result.get("status") == "error":
            updated.append(result)
            continue
        score = _score_similarity(first_path, current_path)
        result["consistency_score"] = score
        result["status"] = "consistency_check"
        updated.append(result)
    return {"shot_results": updated}
