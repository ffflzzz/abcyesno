"""Finalize the whole series run."""

from __future__ import annotations

import os

from mc_state import AgentState, project_root


async def finalize_series(state: AgentState) -> dict:
    """Mark the series complete and write a small playlist manifest."""
    results = state.get("episode_results") or []
    ep_dir = project_root(state)
    playlist_path = os.path.join(ep_dir, "playlist.txt")
    try:
        os.makedirs(ep_dir, exist_ok=True)
        lines = []
        for er in results:
            vp = er.get("final_video_path") or ""
            lines.append(f"ep{er.get('episode')}: {vp}")
        with open(playlist_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except Exception:
        pass
    return {"status": "done", "series_playlist_path": playlist_path}
