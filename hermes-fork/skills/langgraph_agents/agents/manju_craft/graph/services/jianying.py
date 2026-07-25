"""Build Jianying draft_content.json and asset zip."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

TRACK_TYPE_VIDEO = 0
TRACK_TYPE_AUDIO = 1
TRACK_TYPE_TEXT = 4


def _us(seconds: float) -> int:
    return int(seconds * 1_000_000)


def _copy_file(src: str, dst: str) -> None:
    if not src:
        return
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def build_draft_and_zip(
    shot_results: list[dict],
    shots: list[dict],
    project_dir: str,
    *,
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 24,
) -> tuple[str, str]:
    """Return (draft_json_path, zip_path)."""
    assets_dir = os.path.join(project_dir, "assets")
    keyframes_dir = os.path.join(assets_dir, "keyframes")
    os.makedirs(assets_dir, exist_ok=True)

    draft = {
        "draft_version": "10.8",
        "resolution": list(resolution),
        "fps": fps,
        "duration": 0.0,
        "tracks": [],
        "materials": {"videos": [], "audios": [], "texts": []},
    }
    video_track = {"id": "track_video_1", "type": TRACK_TYPE_VIDEO, "name": "视频轨道1", "segments": []}
    audio_track = {"id": "track_audio_1", "type": TRACK_TYPE_AUDIO, "name": "音频轨道1", "segments": []}
    text_track = {"id": "track_text_1", "type": TRACK_TYPE_TEXT, "name": "字幕轨道1", "segments": []}

    duration_by_index = {s["index"]: s["duration"] for s in shots}
    current_us = 0
    for i, result in enumerate(shot_results):
        duration = duration_by_index.get(result["index"], 5.0)
        dur_us = _us(duration)
        start_us = current_us

        if result.get("video_path"):
            video_name = f"shot_{i + 1:03d}.mp4"
            _copy_file(result["video_path"], os.path.join(assets_dir, video_name))
            mat_id = f"mat_video_{i + 1}"
            draft["materials"]["videos"].append({
                "id": mat_id,
                "path": f"assets/{video_name}",
                "duration": dur_us,
                "width": resolution[0],
                "height": resolution[1],
            })
            video_track["segments"].append({
                "material_id": mat_id,
                "target_timerange": {"start": start_us, "duration": dur_us},
                "source_timerange": {"start": 0, "duration": dur_us},
                "speed": 1.0,
                "transform": {
                    "scale_x": 1.0, "scale_y": 1.0,
                    "translate_x": 0.0, "translate_y": 0.0,
                    "rotation": 0.0,
                },
            })

        if result.get("tts_audio_path"):
            audio_name = f"shot_{i + 1:03d}_audio.mp3"
            _copy_file(result["tts_audio_path"], os.path.join(assets_dir, audio_name))
            mat_id = f"mat_audio_{i + 1}"
            draft["materials"]["audios"].append({
                "id": mat_id,
                "path": f"assets/{audio_name}",
                "duration": dur_us,
            })
            audio_track["segments"].append({
                "material_id": mat_id,
                "target_timerange": {"start": start_us, "duration": dur_us},
                "volume": 1.0,
            })

        subtitle = result.get("subtitle", "")
        if subtitle:
            mat_id = f"mat_text_{i + 1}"
            draft["materials"]["texts"].append({
                "id": mat_id,
                "content": subtitle,
                "font_family": "Microsoft YaHei",
                "font_size": 48,
                "text_color": "#FFFFFF",
                "position": {"x": 0.5, "y": 0.85},
            })
            text_track["segments"].append({
                "material_id": mat_id,
                "target_timerange": {"start": start_us, "duration": dur_us},
            })

        if result.get("keyframe_path"):
            kf_name = f"shot_{i + 1:03d}.jpg"
            _copy_file(result["keyframe_path"], os.path.join(keyframes_dir, kf_name))

        current_us += dur_us

    draft["duration"] = current_us / 1_000_000.0
    draft["tracks"] = [video_track, audio_track, text_track]

    draft_path = os.path.join(project_dir, "draft_content.json")
    Path(draft_path).write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = os.path.join(project_dir, "assets.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(draft_path, "draft_content.json")
        for root, _, files in os.walk(assets_dir):
            for file in files:
                full = os.path.join(root, file)
                arc = os.path.relpath(full, project_dir)
                zf.write(full, arc)

    return draft_path, zip_path
