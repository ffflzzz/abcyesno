"""Generate TTS audio per shot."""

from __future__ import annotations

import asyncio
import os

from mc_services.tts import adjust_audio_duration, generate_tts
from mc_state import AgentState, episode_project_dir


async def generate_tts_node(state: AgentState) -> dict:
    """Generate and normalize TTS audio for shots with dialogue.

    DEFAULT-OFF: Agnes Video V2.0 generates lip-synced speech from the video
    prompt itself (verified 2026-08-23), so the separate TTS pass is redundant
    by default — running it would stack a second voice track on top of the
    model's native dialogue. Set ``state["tts_enabled"] = True`` to opt back in
    (e.g. to force a specific external dub voice or when the video model's
    native speech is undesirable). When off, this node simply passes
    ``shot_results`` through untouched, so ``merge_and_concat`` concatenates
    the videos' native audio tracks.
    """
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}

    if not state.get("tts_enabled"):
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
