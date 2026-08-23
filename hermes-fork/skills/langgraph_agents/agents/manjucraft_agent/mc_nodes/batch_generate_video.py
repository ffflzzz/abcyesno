"""Generate short videos from keyframes."""

from __future__ import annotations

import asyncio
import os
import re

from mc_services.agnes_media import generate_video_to_file, video_on_fallback
from mc_services.ffmpeg import extract_last_frame
from mc_state import AgentState, episode_project_dir

# Inter-shot continuity bridge: when generating shot N (N>0), extract the last
# frame of shot N-1's finished video and feed it as the first-frame reference
# (image) for shot N. This gives the model a visual anchor so the next clip
# starts where the previous one ended — the most reliable continuity lever we
# have, since agnes-video-v2.0 does NOT honor endpoint-pinned keyframes across
# unrelated shots (see docs/AGNES_VIDEO_KEYFRAMES_REALITY.md).
_PREV_FRAME_BRIDGE = True


def _duration_to_frames(duration: float) -> int:
    """Pick a num_frames value that approximates duration at 24fps and satisfies 8n+1."""
    import math

    target = int(duration * 24)
    n = max(1, (target - 1) // 8)
    n = min(n, 55)  # 8*55+1 = 441
    return 8 * n + 1


# Camera-movement hints: 中文 → English phrase (see batch_generate_keyframes).
MOTION_EN = {
    "固定": "static shot, locked camera, no camera movement",
    "推进": "slow push in, camera dollies forward toward the subject",
    "后退": "slow pull out, camera dollies backward away from the subject",
    "左摇": "camera pans left across the scene",
    "右摇": "camera pans right across the scene",
    "上移": "camera tilts up",
    "下移": "camera tilts down",
    "旋转": "camera slowly orbits around the subject",
}


def _video_dims(res: str) -> tuple[int, int]:
    """Map the agent "WxH" resolution to video width/height (capped for the API)."""
    m = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", str(res))
    if not m:
        return (1024, 576)
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return (1024, 576)
    # Cap the long edge at 1280 (Agnes video model practical ceiling) while
    # preserving the chosen aspect ratio.
    long_edge = max(w, h)
    scale = min(1.0, 1280.0 / long_edge)
    vw = max(64, int(round(w * scale / 8) * 8))
    vh = max(64, int(round(h * scale / 8) * 8))
    return (vw, vh)


# When True, dialogue lines are injected into the video prompt so Agnes
# Video V2.0 generates SYNCHRONIZED lip-synced speech in its native audio
# track (verified 2026-08-23: the model DOES speak prompt-supplied lines,
# e.g. a Chinese VO when the prompt asks for it). When False, the video ships
# with only its ambient native audio and the separate TTS node is the only
# source of spoken dialogue. We default to True because the model's native
# audio already contains the lines — running TTS on top would double the voice
# track. The TTS node is still reachable (see mc_graph) for consumers that
# want an explicit external dub.
_USE_NATIVE_DIALOGUE = True


def _build_video_prompt(shot: dict | None, base_prompt: str, motion: str | None) -> str:
    """Compose the final video prompt, appending dialogue for native speech.

    Agnes Video V2.0 generates lip-synced speech from the prompt text itself
    (no dedicated dialogue field). We append the shot's dialogue (if any) as a
    spoken-line directive so the model voices it in the native audio track.
    """
    video_prompt = base_prompt
    if motion and motion != "固定":
        video_prompt = f"{video_prompt}, {MOTION_EN.get(motion, '')}"
    if _USE_NATIVE_DIALOGUE and shot:
        dialogue = (shot.get("dialogue") or "").strip()
        if dialogue:
            # Keep the directive language-agnostic: tell the model to actually
            # speak the line (not just show it) and to lip-sync to it.
            video_prompt = (
                f"{video_prompt}. "
                f"The character speaks the following line aloud, clearly audible, "
                f"lip-synced to the words: “{dialogue}”"
            )
    return video_prompt


async def batch_generate_video(state: AgentState) -> dict:
    """Generate a short video for each shot that has a keyframe."""
    if state.get("stop_requested"):
        return {"shot_results": state.get("shot_results", [])}

    project_dir = episode_project_dir(state)
    shot_results = state.get("shot_results", [])
    shots = state["shots"]
    vw, vh = _video_dims(state.get("resolution") or "1080x1920")

    # Collect every character reference angle (multi-view set) once, deduped,
    # so each shot's video generation can carry them as identity anchors.
    # Forward-compatible: the Agnes video model currently ignores them
    # (VIDEO_SUPPORTS_REFERENCE_IMAGES=False), but the plumbing is in place.
    character_refs: list[str] = []
    _seen = set()
    for c in state.get("characters", []):
        imgs = c.get("view_images") or []
        if not imgs and c.get("ref_image"):
            imgs = [c["ref_image"]]
        for v in imgs:
            if v and v not in _seen:
                _seen.add(v)
                character_refs.append(v)

    async def gen_one(result: dict, prev_last_frame: str | None = None) -> dict:
        if result.get("status") == "error" or not result.get("keyframe_path"):
            return result
        idx = result["index"]
        shot = next((s for s in shots if s["index"] == idx), None)
        duration = shot["duration"] if shot else 5.0
        num_frames = _duration_to_frames(duration)
        out_path = os.path.join(project_dir, "videos", f"shot_{idx:03d}.mp4")
        base_video_prompt = shot["video_prompt"] if shot else "subtle cinematic motion"
        motion = shot.get("motion") if shot else None
        video_prompt = _build_video_prompt(shot, base_video_prompt, motion)
        # Frame bridge (debt #7): prefer a user-uploaded first frame over the
        # auto-generated keyframe; when both first+last frames are supplied,
        # drive the model in keyframes mode so the clip spans the intended
        # transition. Forward-compatible: Agnes ignores these when unsupported.
        first_url = shot.get("first_frame_url") if shot else None
        last_url = shot.get("last_frame_url") if shot else None
        # Inter-shot continuity bridge (prev-frame): if enabled and the previous
        # shot emitted a last-frame PNG, use it as the first-frame reference
        # UNLESS the user explicitly uploaded a first frame (explicit > auto).
        if not first_url and prev_last_frame and os.path.exists(prev_last_frame):
            first_url = prev_last_frame
        image = first_url or result.get("keyframe_path")
        keyframes = [first_url, last_url] if (first_url and last_url) else None
        try:
            await generate_video_to_file(
                video_prompt,
                output_path=out_path,
                image=image,
                keyframes=keyframes,
                reference_images=character_refs if character_refs else None,
                width=vw, height=vh,
                num_frames=num_frames, frame_rate=24,
            )
            result["video_path"] = out_path
            result["status"] = "video_ok"
            # Extract this shot's last frame for the NEXT shot's bridge.
            if _PREV_FRAME_BRIDGE:
                last_png = os.path.join(project_dir, "keyframes", f"shot_{idx:03d}_last.png")
                try:
                    extract_last_frame(out_path, last_png)
                    result["last_frame_path"] = last_png
                except Exception:
                    pass
        except Exception as exc:
            result["status"] = "video_fail"
            result["error"] = str(exc)
        return result

    # Process shots in ORDER and serially when the prev-frame bridge is on:
    # shot N needs shot N-1's extracted last frame as its first-frame anchor.
    # (Rate limiting still happens inside generate_video_to_file via the RPM
    # limiter, so serializing here only affects ordering, not API pacing.)
    updated: list[dict] = []
    prev_last: str | None = None
    for r in shot_results:
        r = await gen_one(r, prev_last_frame=prev_last if _PREV_FRAME_BRIDGE else None)
        updated.append(r)
        prev_last = r.get("last_frame_path")
    return {"shot_results": updated}
