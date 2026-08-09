"""Plan a series: split the full series script into per-episode scripts."""

from __future__ import annotations

from mc_services.llm import split_series_script
from mc_state import AgentState


async def plan_episodes(state: AgentState) -> dict:
    """Fill ``episode_scripts`` for series mode (idempotent for single)."""
    mode = state.get("mode", "single")
    if mode != "series":
        # Single mode: ensure episode_scripts == [script].
        if not state.get("episode_scripts"):
            return {"episode_scripts": [state.get("script", "")]}
        return {}

    if state.get("episode_scripts"):
        return {}  # already planned (e.g. resumed from checkpoint)

    total = int(state.get("total_episodes", 1) or 1)
    series_script = state.get("series_script", "")
    episodes = await split_series_script(series_script, total)
    return {"episode_scripts": episodes, "status": "planning"}
