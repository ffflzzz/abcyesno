"""Generate the Jianying draft JSON and asset zip."""

from __future__ import annotations

import os

from graph.services.jianying import build_draft_and_zip
from graph.state import AgentState


async def generate_jianying_draft(state: AgentState) -> dict:
    """Build draft_content.json and assets.zip for Jianying import."""
    project_dir = _project_dir(state)
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


def _project_dir(state: AgentState) -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".manjucraft", "projects", state["project_name"])
