"""Parse the current episode's script into shots and characters."""

from __future__ import annotations

from mc_services.llm import parse_script_to_shots
from mc_state import AgentState


async def parse_script(state: AgentState) -> dict:
    """Parse episode_scripts[current_episode] into shots + characters.

    Steering notes (if any from a prior gate) are folded into the parse prompt
    so the user's adjustment actually takes effect (debt #5).
    """
    state["status"] = "parsing"
    ep = int(state.get("current_episode", 0) or 0)
    scripts = state.get("episode_scripts") or []
    if ep < len(scripts):
        script = scripts[ep]
    else:
        script = state.get("script", "")

    steer = (state.get("steer_notes") or "").strip()
    if steer:
        script = f"[用户修改意见：{steer}]\n{script}"

    parsed = await parse_script_to_shots(script)

    if isinstance(parsed, list):
        raw_shots = parsed
        raw_characters = []
    else:
        raw_shots = parsed.get("shots", [])
        raw_characters = parsed.get("characters", [])

    shots = []
    for i, raw in enumerate(raw_shots):
        shots.append({
            "index": raw.get("index", i),
            "description": raw.get("description", ""),
            "dialogue": raw.get("dialogue", ""),
            "duration": float(raw.get("duration", 5.0)),
            "prompt": raw.get("prompt", ""),
            "video_prompt": raw.get("video_prompt", ""),
        })

    characters = []
    for raw in raw_characters:
        characters.append({
            "name": raw.get("name", ""),
            "prompt": raw.get("prompt", ""),
            "ref_image": "",
        })

    return {
        "script": script,
        "shots": shots,
        "characters": characters,
        "total_shots": len(shots),
        "completed_shots": 0,
        "current_shot_index": 0,
        "status": "idle",
    }
