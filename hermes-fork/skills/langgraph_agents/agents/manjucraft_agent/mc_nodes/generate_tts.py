"""Generate TTS audio per shot."""

from __future__ import annotations

import asyncio
import os

from mc_services.tts import adjust_audio_duration, generate_tts
from mc_state import AgentState, episode_project_dir


async def generate_tts_node(state: AgentState) -> dict:
    """Generate and normalize TTS audio for shots with dialogue."""
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}

    project_dir = episode_project_dir(state)
    shot_results = state.get("shot_results", [])
    shots = state["shots"]

    async def gen_one(result: dict) -> dict:
        idx = result["index"]
        shot = next((s for s in shots if s["index"] == idx), None)
        dialogue = shot["dialogue"] if shot else ""
        duration = shot["duration"] if shot else 5.0
        if not dialogue or result.get("status") == "error":
            return result
        raw_path = os.path.join(project_dir, "audio", f"shot_{idx:03d}_raw.mp3")
        out_path = os.path.join(project_dir, "audio", f"shot_{idx:03d}.mp3")
        try:
            await generate_tts(dialogue, raw_path)
            adjust_audio_duration(raw_path, out_path, duration)
            result["tts_audio_path"] = out_path
        except Exception as exc:
            result["error"] = str(exc)
        return result

    updated = await asyncio.gather(*(gen_one(r) for r in shot_results))
    return {"shot_results": updated}
