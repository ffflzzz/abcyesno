"""Generate the Jianying draft JSON and asset zip."""

from __future__ import annotations

from mc_services.jianying import build_draft_and_zip
from mc_state import AgentState, episode_project_dir


async def generate_jianying_draft(state: AgentState) -> dict:
    """Build draft_content.json and assets.zip for Jianying import."""
    project_dir = episode_project_dir(state)
    draft_path, zip_path = build_draft_and_zip(
        state.get("shot_results", []),
        state.get("shots", []),
        project_dir,
    )
    return {
        "jianying_draft_path": draft_path,
        "assets_zip_path": zip_path,
        "status": "done",
    }
