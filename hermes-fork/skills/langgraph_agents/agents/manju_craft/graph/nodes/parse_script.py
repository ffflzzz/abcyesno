"""Parse user script into shots and characters."""

from __future__ import annotations

from graph.services.llm import parse_script_to_shots
from graph.state import AgentState


async def parse_script(state: AgentState) -> dict:
    """Parse the script into shots and extract characters."""
    state["status"] = "parsing"
    script = state["script"]
    parsed = await parse_script_to_shots(script)

    # The LLM sometimes returns a list directly, sometimes an object.
    if isinstance(parsed, list):
        raw_shots = parsed
        raw_characters = []
    else:
        raw_shots = parsed.get("shots", [])
        raw_characters = parsed.get("characters", [])

    # Ensure every shot has the required keys and a sane duration.
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
        "shots": shots,
        "characters": characters,
        "total_shots": len(shots),
        "completed_shots": 0,
        "current_shot_index": 0,
        "status": "idle",
    }
