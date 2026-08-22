"""Agnes AI image/video generation wrappers (debt #3: poll URL from BASE_URL; debt #4: mock via env)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"
MOCK = bool(os.environ.get("MANJUCRAFT_AGENT_MOCK"))

# Whether the Agnes video model accepts ``reference_images`` (character/identity
# anchors). Currently FALSE — the model ignores them, so we plumb the param
# through but do NOT send it. Flip to True once a video model supports it; no
# other code change is then required for multi-view character consistency.
VIDEO_SUPPORTS_REFERENCE_IMAGES = False

# ---------------------------------------------------------------------------
# Video key fallback + daily quota bookkeeping (Token Plan = 500s/day, RPM 5;
# public/default key = unlimited seconds, RPM 1). When the Token Plan daily
# quota is exhausted (or a 429 comes back), we transparently fall back to the
# public key, which is rate-limited to 1 RPM so calls must be serialized.
# ---------------------------------------------------------------------------
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes_portable_data"))
_VIDEO_USAGE_FILE = os.path.join(HERMES_HOME, "manjucraft_agent", "video_quota_usage.json")
# Token Plan daily video-second quota (see https://www.agnes-ai.com/zh-Hans/docs/tokenplan)
TOKEN_PLAN_DAILY_VIDEO_SECONDS = int(os.environ.get("AGNES_VIDEO_DAILY_QUOTA", "500"))
# Once we fall back to the public key, serialize every call (RPM 1).
_FALLBACK_SERIAL_LOCK = asyncio.Lock()
# Process-wide flag: True once any video call has switched to the fallback key.
# Lets the batch node drop to serialized execution for the remaining shots.
_VIDEO_ON_FALLBACK = False


def video_on_fallback() -> bool:
    return _VIDEO_ON_FALLBACK


class _RpmLimiter:
    """Simple async token-bucket rate limiter honoring Agnes RPM caps.

    Agnes enforces Requests-Per-Minute, not concurrency. A Semaphore only caps
    in-flight requests, which still bursts past the RPM window. This limiter
    enforces a minimum spacing (``60/rpm`` seconds) between *starts* of calls
    sharing the same key, so N shots never exceed the published RPM.
    """

    def __init__(self, rpm: int) -> None:
        self._rpm = max(1, rpm)
        self._gap = 60.0 / self._rpm
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._gap - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# Primary (Token Plan) key: 5 RPM -> keep a safe 4 RPM headroom.
_PRIMARY_RPM_LIMITER = _RpmLimiter(int(os.environ.get("AGNES_VIDEO_PRIMARY_RPM", "4")))
# Fallback (public) key: strictly 1 RPM.
_FALLBACK_RPM_LIMITER = _RpmLimiter(1)


def _primary_api_key() -> str:
    key = os.environ.get("AGNES_API_KEY")
    if not key:
        raise RuntimeError("AGNES_API_KEY not set")
    return key


def _fallback_api_key() -> str | None:
    """Public/default key used when the Token Plan quota is exhausted.

    Configured via AGNES_FALLBACK_API_KEY (env, injected by hermes-runner) or
    read directly from HERMES_HOME/.env so the agent also works if the Electron
    bridge is bypassed. An explicitly-empty env var (set to "") disables the
    fallback even if .env has a value, which is useful for testing.
    """
    env_key = os.environ.get("AGNES_FALLBACK_API_KEY")
    if env_key is not None:
        # Explicitly set (even to "") takes precedence over .env.
        if env_key == "":
            return None
        return env_key
    env_key = os.environ.get("AGNES_PUBLIC_API_KEY")
    if env_key:
        return env_key
    # Fall back to parsing HERMES_HOME/.env (same source the Electron bridge uses).
    try:
        text = Path(os.path.join(HERMES_HOME, ".env")).read_text("utf-8")
        m = re.search(r"^AGNES_FALLBACK_API_KEY=(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _load_video_usage() -> dict:
    """Return {date, seconds} for the local daily video-second ledger."""
    try:
        data = json.loads(Path(_VIDEO_USAGE_FILE).read_text("utf-8"))
    except Exception:
        data = {}
    if data.get("date") != _today_key():
        # New local day -> reset ledger (remote quota also resets at Agnes' TZ).
        return {"date": _today_key(), "seconds": 0}
    return data


def _add_video_usage(seconds: float) -> None:
    """Persist consumed video seconds to the local daily ledger."""
    try:
        data = _load_video_usage()
        data["seconds"] = float(data.get("seconds", 0)) + float(seconds)
        Path(_VIDEO_USAGE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(_VIDEO_USAGE_FILE).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    except Exception:
        pass


def _token_plan_quota_remaining() -> float:
    """Seconds left in the Token Plan daily quota (local estimate)."""
    if not _fallback_api_key():
        # No fallback configured: always use the primary key, never "exhausted".
        return float("inf")
    used = float(_load_video_usage().get("seconds", 0))
    return max(0.0, TOKEN_PLAN_DAILY_VIDEO_SECONDS - used)


def _is_quota_exceeded_error(exc: Exception) -> bool:
    """Detect Agnes quota/rate-limit responses worth a key fallback.

    Empirically the video create endpoint returns **429** when the Token Plan
    daily-second quota or RPM cap is hit (the agnes-video-v20 doc lists
    400/401/404/500/503 but the live API also emits 429 on over-quota). We treat
    429 as an unconditional over-quota signal, and also match 401/400/403 whose
    body mentions quota/limit/plan as a belt-and-suspenders fallback.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return True
        try:
            body = exc.response.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            msg = " ".join(str(v) for v in body.values() if isinstance(v, (str, int))).lower()
        else:
            msg = str(body).lower()
        if exc.response.status_code in (401, 400, 403):
            return any(k in msg for k in ("quota", "exceed", "limit", "plan", "subscription", "too many"))
    return False


def _result_video_url(data: dict) -> str | None:
    """Extract the finished video URL from a poll response.

    Per agnes-video-v20 docs, the URL lives at ``metadata.url`` and is only
    present when ``status == "completed"``. Older/id-style responses may nest it
    differently, so we probe both ``metadata.url`` and a top-level ``url``.
    """
    if isinstance(data.get("metadata"), dict) and data["metadata"].get("url"):
        return data["metadata"]["url"]
    if data.get("url"):
        return data["url"]
    return None


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


def _is_retryable_http_error(exc: Exception) -> bool:
    """Classify an httpx error as transient (worth retrying) or fatal.

    Retry: connection/timeout/transport failures + 5xx server errors (the
    503 in the bug report). Do NOT retry 4xx (bad request / auth / param
    errors) — those are caller bugs and will never succeed on retry.
    """
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return exc.response.status_code >= 500
        except Exception:
            return True
    return False


async def _post_json_with_retry(url: str, *, headers: dict, json: dict, timeout: float, max_attempts: int = 3) -> "httpx.Response":
    """POST JSON with bounded exponential backoff on transient failures.

    Surfaces 4xx immediately (no retry); retries 5xx / transport / timeout up
    to ``max_attempts`` times with ``2 ** attempt`` second gaps.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=json)
            # Raise only after the transport layer succeeded, so 4xx (no retry)
            # is distinguished from a transient 5xx (retry).
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 - we re-raise deliberately
            last_exc = exc
            if not _is_retryable_http_error(exc):
                raise
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc


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

    resp = await _post_json_with_retry(
        f"{BASE_URL}/images/generations",
        headers={"Authorization": f"Bearer {_primary_api_key()}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
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
        # Download the result image; retry transient fetch failures too.
        last_dl_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    dl = await client.get(url)
                dl.raise_for_status()
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(dl.content)
                return output_path
            except Exception as exc:  # noqa: BLE001
                last_dl_err = exc
                if not _is_retryable_http_error(exc):
                    raise
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
        assert last_dl_err is not None
        raise last_dl_err
    return url or ""


async def create_video(
    prompt: str,
    *,
    image: str | None = None,
    keyframes: list[str] | None = None,
    reference_images: list[str] | None = None,
    width: int = 1152,
    height: int = 768,
    num_frames: int = 121,
    frame_rate: int = 24,
    timeout: float = 60.0,
    api_key: str | None = None,
) -> str:
    """Create an async video task. Returns video_id.

    ``api_key`` lets callers pick the primary (Token Plan) or fallback (public)
    key. When omitted, the primary AGNES_API_KEY is used.
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
        # 关键帧动画：文档参数表声明顶层 `mode` 可取 "keyframes"，示例又把
        # mode 与 image 数组放在 extra_body 里。两种写法都发出去，确保网关
        # 无论读顶层还是 extra_body 都能识别。image 数组始终走 extra_body
        # （文档示例如此；顶层 image 是单图图生视频字段）。
        kf_images = [
            p if p.startswith("http") or p.startswith("data:") else _data_uri_from_file(p)
            for p in keyframes
        ]
        body["mode"] = "keyframes"
        body["extra_body"] = {"image": kf_images, "mode": "keyframes"}
    elif image:
        body["image"] = image if image.startswith("http") or image.startswith("data:") else _data_uri_from_file(image)
        body["mode"] = "ti2vid"

    # Character/identity reference anchors for consistency. No-op while the
    # Agnes video model does not accept them (VIDEO_SUPPORTS_REFERENCE_IMAGES).
    # Plumbed now so a future model can consume multi-view character refs
    # without rewiring the call sites.
    if reference_images and VIDEO_SUPPORTS_REFERENCE_IMAGES:
        body.setdefault("extra_body", {})
        body["extra_body"]["reference_images"] = [
            p if p.startswith("http") or p.startswith("data:") else _data_uri_from_file(p)
            for p in reference_images
        ]

    auth_key = api_key or _primary_api_key()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{BASE_URL}/videos",
            headers={"Authorization": f"Bearer {auth_key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["video_id"]


async def poll_video(video_id: str, *, timeout: float = 600.0, interval: float = 5.0, api_key: str | None = None) -> dict:
    """Poll until the video task completes or fails (endpoint derived from BASE_URL)."""
    auth_key = api_key or _primary_api_key()
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            resp = await client.get(
                _poll_base(),
                params={"video_id": video_id},
                headers={"Authorization": f"Bearer {auth_key}"},
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
    reference_images: list[str] | None = None,
    width: int = 1152,
    height: int = 768,
    num_frames: int = 121,
    frame_rate: int = 24,
    max_attempts: int = 3,
) -> str:
    """Create and download a video to output_path. Returns output_path.

    Implements Token-Plan -> public-key fallback:
      * Primary key is the user's Token Plan key (500s/day, 5 RPM).
      * If the local daily ledger predicts exhaustion, OR a 429/quota error is
        observed, fall back to AGNES_FALLBACK_API_KEY (unlimited seconds, 1 RPM).
      * The fallback path is serialized through a global lock to honor 1 RPM.
    """
    if MOCK:
        return _mock_video_file(output_path)

    # Decide whether to start on the fallback key up-front (quota predicted
    # exhausted). We always try the primary first unless it's clearly spent.
    use_fallback = _token_plan_quota_remaining() <= 0
    fb_key = _fallback_api_key()

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            # Pick the key for this attempt.
            if use_fallback:
                if not fb_key:
                    raise RuntimeError("Token Plan video quota exhausted and no AGNES_FALLBACK_API_KEY configured")
                # Public key: strictly 1 RPM -> rate-limit + serialize.
                async with _FALLBACK_SERIAL_LOCK:
                    await _FALLBACK_RPM_LIMITER.acquire()
                    video_id = await create_video(
                        prompt, image=image, keyframes=keyframes,
                        reference_images=reference_images, width=width, height=height,
                        num_frames=num_frames, frame_rate=frame_rate, timeout=120,
                        api_key=fb_key,
                    )
                    result = await poll_video(video_id, api_key=fb_key)
            else:
                # Token Plan primary key: honor 4 RPM headroom.
                await _PRIMARY_RPM_LIMITER.acquire()
                video_id = await create_video(
                    prompt, image=image, keyframes=keyframes,
                    reference_images=reference_images, width=width, height=height,
                    num_frames=num_frames, frame_rate=frame_rate, timeout=120,
                )
                result = await poll_video(video_id)

            url = _result_video_url(result)
            if not url:
                raise RuntimeError("completed video has no url")
            async with httpx.AsyncClient(timeout=120) as client:
                raw = (await client.get(url)).content
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(raw)

            # Book the consumed seconds only on the primary (Token Plan) key.
            if not use_fallback:
                _add_video_usage(num_frames / frame_rate)
            return output_path
        except Exception as exc:
            last_error = exc
            # On quota/429, switch to the fallback key for the next attempt.
            if (not use_fallback) and fb_key and _is_quota_exceeded_error(exc):
                use_fallback = True
                global _VIDEO_ON_FALLBACK
                _VIDEO_ON_FALLBACK = True
                continue
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
    raise last_error or RuntimeError("video generation failed")
