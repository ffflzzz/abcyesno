"""Edge-TTS wrapper for dialogue dubbing (debt #4: mock writes offline stub)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import edge_tts

from mc_services.agnes_media import MOCK

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


async def generate_tts(
    text: str,
    output_path: str,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    volume: str = "+0%",
) -> str:
    """Generate a TTS audio file. In mock mode writes a placeholder mp3."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if MOCK:
        Path(output_path).write_bytes(b"mock-mp3-placeholder")
        return output_path
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(output_path)
    return output_path


def adjust_audio_duration(input_path: str, output_path: str, target_seconds: float) -> str:
    """Trim/pad audio to target_seconds. In mock mode just copies the stub."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if MOCK:
        data = Path(input_path).read_bytes() if os.path.exists(input_path) else b"mock-mp3-placeholder"
        Path(output_path).write_bytes(data)
        return output_path

    ffmpeg = os.environ.get("FFMPEG_PATH", "./bin/ffmpeg.exe")
    if not os.path.exists(ffmpeg):
        ffmpeg = "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-af", f"adelay=0|0,apad=pad_dur={target_seconds}",
        "-t", str(target_seconds), "-c:a", "libmp3lame", "-b:a", "128k",
        output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path
