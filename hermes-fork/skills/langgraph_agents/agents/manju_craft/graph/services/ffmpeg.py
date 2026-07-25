"""FFmpeg command builders and helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def ffmpeg_path() -> str:
    """Return the path to the bundled or system ffmpeg executable."""
    bundled = "./bin/ffmpeg.exe"
    if os.path.exists(bundled):
        return bundled
    return "ffmpeg"


def run_ffmpeg(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given arguments."""
    cmd = [ffmpeg_path(), *args]
    return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def normalize_audio(input_path: str, output_path: str, target_seconds: float) -> str:
    """Trim or pad an audio file to exactly target_seconds."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_ffmpeg([
        "-y", "-i", input_path,
        "-af", "adelay=0|0,apad=pad_dur=%s" % target_seconds,
        "-t", str(target_seconds),
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        output_path,
    ])
    return output_path


def make_concat_list_file(paths: list[str], list_path: str) -> None:
    """Write an ffmpeg concat demuxer list file."""
    os.makedirs(os.path.dirname(list_path) or ".", exist_ok=True)
    lines = [f"file '{Path(p).as_posix()}'" for p in paths]
    Path(list_path).write_text("\n".join(lines), encoding="utf-8")


def concat_media(input_list_path: str, output_path: str) -> str:
    """Concatenate media files using the concat demuxer."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_ffmpeg([
        "-y", "-f", "concat", "-safe", "0",
        "-i", input_list_path,
        "-c", "copy",
        output_path,
    ])
    return output_path


def merge_video_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    *,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    audio_bitrate: str = "128k",
) -> str:
    """Mux a video file with an audio file."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_ffmpeg([
        "-y", "-i", video_path, "-i", audio_path,
        "-c:v", video_codec,
        "-preset", "medium",
        "-crf", "23",
        "-c:a", audio_codec,
        "-b:a", audio_bitrate,
        "-shortest",
        output_path,
    ])
    return output_path


def merge_shots(
    video_paths: list[str],
    audio_paths: list[str | None],
    output_path: str,
) -> str:
    """Concatenate videos and audios separately, then mux into final output.

    Missing audio entries are treated as silent gaps of the same duration as
    the corresponding video.
    """
    # Build per-shot clips with audio when available.
    clip_paths: list[str] = []
    tmp_files: list[str] = []
    for i, (vpath, apath) in enumerate(zip(video_paths, audio_paths)):
        if apath and os.path.exists(apath):
            clip = os.path.join(os.path.dirname(output_path) or ".", f"clip_{i:03d}.mp4")
            merge_video_audio(vpath, apath, clip)
            clip_paths.append(clip)
            tmp_files.append(clip)
        else:
            clip_paths.append(vpath)

    list_path = os.path.join(os.path.dirname(output_path) or ".", "video_concat_list.txt")
    make_concat_list_file(clip_paths, list_path)
    concat_media(list_path, output_path)

    for tmp in tmp_files:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return output_path
