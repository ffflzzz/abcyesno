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
from mc_state import AgentState, CHARACTER_VIEWS, episode_project_dir


async def generate_characters(state: AgentState) -> dict:
    """Generate a multi-view reference set per character, or reuse the bible."""
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
        base_prompt = char.get("prompt", "")
        if steer:
            base_prompt = f"{base_prompt}, 用户修改：{steer}"
        if not base_prompt:
            return char
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name))
        view_images: list[str] = []
        front_path: str | None = None
        for vkey, vcn in CHARACTER_VIEWS:
            vprompt = (
                f"{base_prompt}, {vcn}视角, character reference sheet, "
                f"consistent design, same outfit and facial features, "
                f"clean background"
            )
            out_path = os.path.join(project_dir, "characters", f"{safe}_{vkey}.png")
            await generate_image(vprompt, size="1024x1024", output_path=out_path)
            view_images.append(out_path)
            if vkey == "front":
                front_path = out_path
        char["view_images"] = view_images
        # Canonical front view doubles as the primary identity anchor consumed
        # by keyframe/video generation (forward-compatible).
        char["ref_image"] = front_path or (view_images[0] if view_images else "")
        return char

    updated = await asyncio.gather(*(gen_one(i, c) for i, c in enumerate(characters)))
    return {"characters": updated}
