"""Parse the current episode's script into shots and characters."""

from __future__ import annotations

from mc_services.llm import parse_script_to_shots
from mc_state import AgentState


# Default camera-movement rotation so workflow-generated shots get varied
# cinematography instead of every shot being a static lock-off. The user can
# still override per-shot motion from the Studio storyboard editor (debt #7).
DEFAULT_MOTION_CYCLE = ["固定", "推进", "右摇", "左摇", "后退", "上移", "下移", "旋转"]


async def parse_script(state: AgentState) -> dict:
    """Parse episode_scripts[current_episode] into shots + characters.

    Steering notes (if any from a prior gate) are folded into the parse prompt
    so the user's adjustment actually takes effect (debt #5).

    Fixed characters (user-supplied) bypass the LLM character parse entirely:
    they are injected verbatim and the LLM is only asked for shots + any extra
    characters it discovers. This guarantees cross-episode consistency for the
    roles the user cares about (debt: fixed_characters).
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

    fixed = state.get("fixed_characters") or []
    # When fixed characters are supplied, don't ask the LLM to invent roles —
    # only to lay out shots (and optionally discover additional minor roles).
    parsed = await parse_script_to_shots(script, skip_characters=bool(fixed))

    if isinstance(parsed, list):
        raw_shots = parsed
        raw_characters = []
    else:
        raw_shots = parsed.get("shots", [])
        raw_characters = parsed.get("characters", [])

    # Override per-shot duration when the user set a global sec_per_shot.
    sec_override = float(state.get("sec_per_shot") or 0.0)

    shots = []
    for i, raw in enumerate(raw_shots):
        dur = float(raw.get("duration", 5.0))
        if sec_override > 0:
            dur = sec_override
        shots.append({
            "index": raw.get("index", i),
            "description": raw.get("description", ""),
            "dialogue": raw.get("dialogue", ""),
            "duration": dur,
            "prompt": raw.get("prompt", ""),
            "video_prompt": raw.get("video_prompt", ""),
            "motion": raw.get("motion") or DEFAULT_MOTION_CYCLE[i % len(DEFAULT_MOTION_CYCLE)],
        })

    # Merge user-uploaded first/last frames (set in the Studio storyboard)
    # so batch_generate_video can honour them. Keyed by shot index (0-based)
    # to match the frontend's n-1 mapping (debt #7 / frame-bridge).
    frame_overrides = state.get("shot_frame_overrides") or {}
    if frame_overrides:
        for shot in shots:
            ov = frame_overrides.get(str(shot.get("index"))) or frame_overrides.get(shot.get("index"))
            if isinstance(ov, dict):
                if ov.get("first_frame_url"):
                    shot["first_frame_url"] = ov["first_frame_url"]
                if ov.get("last_frame_url"):
                    shot["last_frame_url"] = ov["last_frame_url"]

    characters = []
    seen_names = set()
    if fixed:
        # User-supplied roles take precedence and are locked verbatim. No
        # ref_image yet — generate_characters fills it (ep0) or reuses bible.
        for c in fixed:
            name = c.get("name", "").strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            characters.append({
                "name": name,
                "prompt": c.get("prompt", ""),
                "ref_image": c.get("ref_image", "") or "",
            })
    for raw in raw_characters:
        name = (raw.get("name") or "").strip()
        if not name or name.lower() in seen_names:
            # Skip LLM-echoed duplicates of user-fixed roles.
            continue
        seen_names.add(name.lower())
        characters.append({
            "name": name,
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
