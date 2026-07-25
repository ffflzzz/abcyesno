"""Generate character reference images."""

from __future__ import annotations

import asyncio
import os

from graph.services.agnes_media import generate_image
from graph.state import AgentState


async def generate_characters(state: AgentState) -> dict:
    """Generate one reference image per character."""
    project_dir = _project_dir(state)
    characters = state.get("characters", [])

    if not characters:
        return {"characters": characters}

    async def gen_one(idx: int, char: dict) -> dict:
        name = char.get("name", f"character_{idx}")
        prompt = char.get("prompt", "")
        if not prompt:
            return char
        out_path = os.path.join(project_dir, "characters", f"{name}.png")
        await generate_image(
            prompt,
            size="1024x1024",
            output_path=out_path,
        )
        char["ref_image"] = out_path
        return char

    updated = await asyncio.gather(*(gen_one(i, c) for i, c in enumerate(characters)))
    return {"characters": updated}


def _project_dir(state: AgentState) -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".manjucraft", "projects", state["project_name"])
