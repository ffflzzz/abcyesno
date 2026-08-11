"""Generate the Jianying draft JSON and asset zip."""

from __future__ import annotations

import re

from mc_services.jianying import build_draft_and_zip
from mc_state import AgentState, episode_project_dir


def _resolution_tuple(res: str) -> tuple[int, int]:
    """Parse the agent "WxH" resolution into (w, h) for the Jianying draft."""
    m = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", str(res))
    if not m:
        return (1080, 1920)
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return (1080, 1920)
    return (w, h)


async def generate_jianying_draft(state: AgentState) -> dict:
    """Build draft_content.json and assets.zip for Jianying import."""
    project_dir = episode_project_dir(state)
    resolution = _resolution_tuple(state.get("resolution") or "1080x1920")
    draft_path, zip_path = build_draft_and_zip(
        state.get("shot_results", []),
        state.get("shots", []),
        project_dir,
        resolution=resolution,
    )
    return {
        "jianying_draft_path": draft_path,
        "assets_zip_path": zip_path,
        "status": "done",
    }
