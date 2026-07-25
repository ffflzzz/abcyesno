"""Agnes AI image/video generation wrappers."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"


def _api_key() -> str:
    key = os.environ.get("AGNES_API_KEY")
    if not key:
        raise RuntimeError("AGNES_API_KEY not set")
    return key


def _data_uri_from_file(path: str) -> str:
    """Convert a local image file to a data URI for img2img / video input."""
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower().lstrip(".")
    mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


async def generate_image(
    prompt: str,
    size: str = "1024x768",
    *,
    reference_images: list[str] | None = None,
    output_path: str | None = None,
    response_format: str = "b64_json",
    timeout: float = 180.0,
) -> str:
    """Generate an image. Returns local file path if output_path is given, otherwise URL/base64 string."""
    body: dict = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
    }
    if reference_images:
        # Convert local paths to data URIs when needed.
        images = [p if p.startswith("http") or p.startswith("data:") else _data_uri_from_file(p) for p in reference_images]
        body["extra_body"] = {"image": images, "response_format": response_format}
    else:
        if response_format == "b64_json":
            body["return_base64"] = True
        else:
            body["extra_body"] = {"response_format": response_format}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{BASE_URL}/images/generations",
            headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()["data"][0]

    if response_format == "b64_json" and data.get("b64_json"):
        raw = base64.b64decode(data["b64_json"])
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(raw)
            return output_path
        return data["b64_json"]

    url = data.get("url")
    if output_path and url:
        async with httpx.AsyncClient(timeout=60) as client:
            raw = (await client.get(url)).content
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(raw)
        return output_path
    return url or ""


async def create_video(
    prompt: str,
    *,
    image: str | None = None,
    keyframes: list[str] | None = None,
    width: int = 1152,
    height: int = 768,
    num_frames: int = 121,
    frame_rate: int = 24,
    timeout: float = 60.0,
) -> str:
    """Create an async video task. Returns video_id.

    image/keyframes can be URLs or local file paths.
    """
    body: dict = {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
    }
    if keyframes:
        body["extra_body"] = {
            "image": [p if p.startswith("http") or p.startswith("data:") else _data_uri_from_file(p) for p in keyframes],
            "mode": "keyframes",
        }
    elif image:
        body["image"] = image if image.startswith("http") or image.startswith("data:") else _data_uri_from_file(image)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{BASE_URL}/videos",
            headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["video_id"]


async def poll_video(
    video_id: str,
    *,
    timeout: float = 600.0,
    interval: float = 5.0,
) -> dict:
    """Poll until the video task completes or fails."""
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            resp = await client.get(
                "https://apihub.agnes-ai.com/agnesapi",
                params={"video_id": video_id},
                headers={"Authorization": f"Bearer {_api_key()}"},
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                return data
            if status == "failed":
                raise RuntimeError(f"video generation failed: {data.get('error')}")
            await asyncio.sleep(interval)
    raise TimeoutError("video generation timed out")


async def generate_video_to_file(
    prompt: str,
    output_path: str,
    *,
    image: str | None = None,
    keyframes: list[str] | None = None,
    width: int = 1152,
    height: int = 768,
    num_frames: int = 121,
    frame_rate: int = 24,
    max_attempts: int = 3,
) -> str:
    """Create and download a video to output_path. Returns output_path."""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            video_id = await create_video(
                prompt,
                image=image,
                keyframes=keyframes,
                width=width,
                height=height,
                num_frames=num_frames,
                frame_rate=frame_rate,
                timeout=120,
            )
            result = await poll_video(video_id)
            url = result.get("url")
            if not url:
                raise RuntimeError("completed video has no url")
            async with httpx.AsyncClient(timeout=120) as client:
                raw = (await client.get(url)).content
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(raw)
            return output_path
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
    raise last_error or RuntimeError("video generation failed")
