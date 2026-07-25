"""Edge-TTS wrapper for dialogue dubbing."""

from __future__ import annotations

import asyncio
import os

import edge_tts

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


async def generate_tts(
    text: str,
    output_path: str,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    volume: str = "+0%",
) -> str:
    """Generate a TTS audio file using Edge-TTS.

    Args:
        text: The Chinese dialogue text.
        output_path: Where to save the mp3 file.
        voice: Edge-TTS voice name.
        rate: Speed adjustment, e.g. "-10%" or "+10%".
        volume: Volume adjustment.

    Returns:
        The output_path.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(output_path)
    return output_path


def adjust_audio_duration(input_path: str, output_path: str, target_seconds: float) -> str:
    """Trim or pad an audio file to exactly target_seconds using FFmpeg.

    Falls back to the input path if FFmpeg is not available.
    """
    import subprocess

    ffmpeg = os.environ.get("FFMPEG_PATH", "./bin/ffmpeg.exe")
    if not os.path.exists(ffmpeg):
        # Try system ffmpeg
        ffmpeg = "ffmpeg"

    cmd = [
        ffmpeg,
        "-y",
        "-i", input_path,
        "-af", f"adelay=0|0,apad=pad_dur={target_seconds}",
        "-t", str(target_seconds),
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path
