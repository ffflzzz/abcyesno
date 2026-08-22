"""FFmpeg command builders and helpers (debt #4: mock writes offline stub)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from mc_services.agnes_media import MOCK

# repo 根 = hermes-fork/skills/langgraph_agents/agents/manjucraft_agent/mc_services/ffmpeg.py
# 往上 6 级到 abcyesno 工程根
_REPO_ROOT = Path(__file__).resolve().parents[6]
_BUNDLED = _REPO_ROOT / "bin" / "ffmpeg.exe"


def ffmpeg_path() -> str:
    """优先用工程自带 bin/ffmpeg.exe，否则退回 PATH 中的 ffmpeg。"""
    if _BUNDLED.exists():
        return str(_BUNDLED)
    on_path = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if on_path:
        return on_path
    # 最后退回相对路径，让调用方看到明确报错而非静默失败
    return str(_BUNDLED)


def run_ffmpeg(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_path(), *args]
    return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def normalize_audio(input_path: str, output_path: str, target_seconds: float) -> str:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_ffmpeg([
        "-y", "-i", input_path,
        "-af", "adelay=0|0,apad=pad_dur=%s" % target_seconds,
        "-t", str(target_seconds), "-c:a", "libmp3lame", "-b:a", "128k",
        output_path,
    ])
    return output_path


def extract_last_frame(video_path: str, out_png: str) -> str:
    """Extract the final frame of a video to a PNG (for inter-shot bridging)."""
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    run_ffmpeg(["-y", "-sseof", "-0.1", "-i", video_path, "-frames:v", "1", out_png])
    return out_png


def make_concat_list_file(paths: list[str], list_path: str) -> None:
    os.makedirs(os.path.dirname(list_path) or ".", exist_ok=True)
    lines = [f"file '{Path(p).as_posix()}'" for p in paths]
    Path(list_path).write_text("\n".join(lines), encoding="utf-8")


def concat_media(input_list_path: str, output_path: str) -> str:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", input_list_path, "-c", "copy", output_path])
    return output_path


def merge_video_audio(video_path: str, audio_path: str, output_path: str, *, video_codec: str = "libx264", audio_codec: str = "aac", audio_bitrate: str = "128k") -> str:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    run_ffmpeg([
        "-y", "-i", video_path, "-i", audio_path,
        "-c:v", video_codec, "-preset", "medium", "-crf", "23",
        "-c:a", audio_codec, "-b:a", audio_bitrate, "-shortest",
        output_path,
    ])
    return output_path


def merge_shots(video_paths: list[str], audio_paths: list[str | None], output_path: str) -> str:
    """Concatenate videos (+audios) then mux into final output.

    Mock mode: skip ffmpeg, copy the first clip as the final placeholder so the
    pipeline can finish offline (smoke control-flow only).
    """
    if MOCK:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        if video_paths:
            Path(output_path).write_bytes(Path(video_paths[0]).read_bytes() if os.path.exists(video_paths[0]) else b"mock-mp4-final")
        else:
            Path(output_path).write_bytes(b"mock-mp4-final")
        return output_path

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
