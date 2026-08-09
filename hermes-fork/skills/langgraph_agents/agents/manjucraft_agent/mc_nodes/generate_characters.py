"""Generate (or reuse) character reference images.

Series behaviour: episode 0 generates characters and the first-frame gate
locks them into ``character_bible``. Later episodes (policy ``lock_bible``)
reuse the bible instead of regenerating, guaranteeing cross-episode
consistency. ``per_episode`` policy always regenerates.
"""

from __future__ import annotations

import asyncio
import os

from mc_services.agnes_media import generate_image
from mc_state import AgentState, episode_project_dir


async def generate_characters(state: AgentState) -> dict:
    """Generate one reference image per character, or reuse the bible."""
    ep = int(state.get("current_episode", 0) or 0)
    policy = state.get("consistency_policy", "lock_bible")
    bible = state.get("character_bible") or []

    # Reuse the locked bible for later episodes under lock_bible policy.
    if ep > 0 and policy == "lock_bible" and bible:
        return {"characters": [dict(c) for c in bible]}

    characters = state.get("characters", [])
    if not characters:
        return {"characters": characters}

    project_dir = episode_project_dir(state)
    steer = (state.get("steer_notes") or "").strip()

    async def gen_one(idx: int, char: dict) -> dict:
        name = char.get("name", f"character_{idx}")
        prompt = char.get("prompt", "")
        if steer:
            prompt = f"{prompt}, 用户修改：{steer}"
        if not prompt:
            return char
        out_path = os.path.join(project_dir, "characters", f"{name}.png")
        await generate_image(prompt, size="1024x1024", output_path=out_path)
        char["ref_image"] = out_path
        return char

    updated = await asyncio.gather(*(gen_one(i, c) for i, c in enumerate(characters)))
    return {"characters": updated}
