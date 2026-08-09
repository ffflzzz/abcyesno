"""Agnes AI image/video generation wrappers (debt #3: poll URL from BASE_URL; debt #4: mock via env)."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"
MOCK = bool(os.environ.get("MANJUCRAFT_AGENT_MOCK"))


def _api_key() -> str:
    key = os.environ.get("AGNES_API_KEY")
    if not key:
        raise RuntimeError("AGNES_API_KEY not set")
    return key


def _poll_base() -> str:
    """Derive the async video poll base URL from BASE_URL (debt #3).

    BASE_URL is typically ``.../v1``; the video status endpoint lives at
    ``.../agnesapi``. Deriving it keeps us consistent with whatever endpoint
    the deployment configures instead of hardcoding the domain.
    """
    if BASE_URL.endswith("/v1"):
        return BASE_URL[: -len("/v1")] + "/agnesapi"
    return BASE_URL + "/agnesapi"


def _data_uri_from_file(path: str) -> str:
    """Convert a local image file to a data URI for img2img / video input."""
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower().lstrip(".")
    mime = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _mock_gray_png(path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        Image.new("RGB", (1024, 576), color=(128, 128, 128)).save(path)
    except Exception:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n-mock-png")
    return path


def _mock_video_file(path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Placeholder bytes -- enough for smoke control-flow; not a playable video.
    Path(path).write_bytes(b"mock-mp4-placeholder")
    return path


async def generate_image(
    prompt: str,
    size: str = "1024x768",
    *,
    reference_images: list[str] | None = None,
    output_path: str | None = None,
    response_format: str = "b64_json",
    timeout: float = 180.0,
) -> str:
    """Generate an image. Returns local file path if output_path given, else URL/base64."""
    if MOCK:
        assert output_path is not None
        return _mock_gray_png(output_path)

    body: dict = {"model": IMAGE_MODEL, "prompt": prompt, "size": size}
    if reference_images:
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
    """Create an async video task. Returns video_id."""
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


async def poll_video(video_id: str, *, timeout: float = 600.0, interval: float = 5.0) -> dict:
    """Poll until the video task completes or fails (endpoint derived from BASE_URL)."""
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            resp = await client.get(
                _poll_base(),
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
    if MOCK:
        return _mock_video_file(output_path)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            video_id = await create_video(
                prompt, image=image, keyframes=keyframes, width=width, height=height,
                num_frames=num_frames, frame_rate=frame_rate, timeout=120,
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
