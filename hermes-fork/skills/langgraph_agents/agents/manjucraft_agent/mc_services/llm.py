"""Text model wrapper for script parsing / series splitting (debt #4: mock via env)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
DEFAULT_MODEL = os.environ.get("AGNES_TEXT_MODEL", "agnes-2.0-flash")

MOCK = bool(os.environ.get("MANJUCRAFT_AGENT_MOCK"))


def _api_key() -> str:
    key = os.environ.get("AGNES_API_KEY")
    if not key:
        raise RuntimeError("AGNES_API_KEY not set")
    return key


def _is_retryable_http_error(exc: Exception) -> bool:
    """Transient errors worth retrying (5xx / transport / timeout), not 4xx."""
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return exc.response.status_code >= 500
        except Exception:
            return True
    return False


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    response_format: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> str:
    """Call the chat completions endpoint and return the assistant's content.

    Retries transient failures (5xx / connection / timeout) up to 3 times with
    exponential backoff. 4xx (bad request / auth) is surfaced immediately.
    """
    if MOCK:
        # Minimal offline stub: echo a request-flavored placeholder. Real
        # callers below provide structured fallbacks, so this is only a guard.
        return json.dumps({"ok": True})

    body: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        body["response_format"] = response_format

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {_api_key()}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_retryable_http_error(exc):
                raise
            if attempt < 3:
                await asyncio.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc


_SCRIPT_PARSE_SYSTEM_PROMPT = """你是一位专业的漫剧（短剧/短视频）分镜师。
请把用户提供的剧本拆分为分镜列表和角色列表。

输出必须是一个 JSON 对象，包含两个字段：
- shots: 分镜数组，每个元素包含：
  - index: 镜头序号，从 0 开始
  - description: 画面描述（中文）
  - dialogue: 该镜头的台词（如果有则保留，没有则空字符串）
  - duration: 预估时长，单位秒，浮点数，每镜默认 5 秒
  - prompt: 用于 AI 生图的英文提示词，需包含角色、场景、风格、光照、构图
  - video_prompt: 用于 AI 生视频的英文提示词，描述镜头运动和主体动作
- characters: 角色数组，每个元素包含：
  - name: 角色名（中文）
  - prompt: 用于 AI 生图的角色英文描述

只输出 JSON 对象，不要任何解释和 markdown 代码块。字段名必须严格与上面一致。"""


async def parse_script_to_shots(script: str, *, model: str | None = None, skip_characters: bool = False) -> list[dict]:
    """Parse a script into a list of shot dictionaries.

    When ``skip_characters`` is True the role list is fixed by the user, so we
    only ask the model for the shot list (it may still surface additional minor
    roles in ``characters`` which ``parse_script`` will append, but the user's
    fixed roles are never overwritten).
    """
    if MOCK:
        # Deterministic smoke output matching the real LLM JSON shape
        # ({shots, characters}) so the character_bible lock (episode 0 ->
        # later episodes reuse) is observable offline.
        if skip_characters:
            # Fixed-characters mode: model only produces shots, no roles.
            return {
                "shots": [
                    {
                        "index": 0,
                        "description": "烟测试场景（固定角色）",
                        "dialogue": "这是一段烟测试旁白。",
                        "duration": 3.0,
                        "prompt": "A gray placeholder scene for smoke testing, cinematic, consistent style",
                        "video_prompt": "Subtle camera movement on a gray placeholder scene",
                    }
                ],
                "characters": [],
            }
        return {
            "shots": [
                {
                    "index": 0,
                    "description": "烟测试场景",
                    "dialogue": "这是一段烟测试旁白。",
                    "duration": 3.0,
                    "prompt": "A gray placeholder scene for smoke testing, cinematic, consistent style",
                    "video_prompt": "Subtle camera movement on a gray placeholder scene",
                }
            ],
            "characters": [
                {
                    "name": "smoke_cat",
                    "prompt": "a gray cartoon cat character, consistent design, flat style",
                }
            ],
        }

    messages = [
        {"role": "system", "content": _SCRIPT_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": f"剧本：\n{script}\n\n请输出分镜 JSON 数组。"},
    ]
    content = await chat_completion(messages, model=model, response_format={"type": "json_object"})
    content = content.strip()
    if content.startswith("```"):
        content = content.removeprefix("```json").removeprefix("```")
        content = content.removesuffix("```").strip()

    parsed = json.loads(content)
    if isinstance(parsed, dict):
        for key in ("shots", "data", "result", "items"):
            if key in parsed:
                parsed = parsed[key]
                break
        else:
            parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"Expected list of shots, got {type(parsed).__name__}")
    return parsed


_SERIES_SPLIT_SYSTEM_PROMPT = """你是一位漫剧编剧助手。用户会给你一部连载短剧的大纲/剧情，以及目标集数 N。
请把整部剧情拆分为 N 集，每集给出一段完整的、可直接作为单集分镜脚本的中文剧情文本。

输出必须是一个 JSON 对象，字段为：
- episodes: 字符串数组，长度恰好为 N，每个元素是该集的独立剧情脚本（中文，包含该集要表现的场景与对话要点）

只输出 JSON 对象，不要任何解释和 markdown 代码块。"""


async def split_series_script(series_script: str, total_episodes: int) -> list[str]:
    """Split a full series script into per-episode scripts via LLM.

    Falls back to an even character-count heuristic when offline / mock / parse
    failure, so the series loop can always proceed.
    """
    if MOCK:
        return _heuristic_split(series_script, total_episodes)

    try:
        messages = [
            {"role": "system", "content": _SERIES_SPLIT_SYSTEM_PROMPT},
            {"role": "user", "content": f"集数：{total_episodes}\n整部剧情：\n{series_script}"},
        ]
        content = await chat_completion(messages, response_format={"type": "json_object"})
        content = content.strip()
        if content.startswith("```"):
            content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(content)
        eps = parsed.get("episodes") if isinstance(parsed, dict) else parsed
        if isinstance(eps, list) and 1 <= len(eps) <= total_episodes + 2:
            # Pad/trim to exactly total_episodes.
            eps = [str(e) for e in eps]
            while len(eps) < total_episodes:
                eps.append(series_script)
            return eps[:total_episodes]
    except Exception:
        pass
    return _heuristic_split(series_script, total_episodes)


def _heuristic_split(series_script: str, total_episodes: int) -> list[str]:
    """Even-ish split by sentence boundaries (offline fallback)."""
    sentences = [s.strip() for s in re.split(r"[。！？\n]+", series_script) if s.strip()]
    if not sentences:
        sentences = [series_script]
    per = max(1, (len(sentences) + total_episodes - 1) // total_episodes)
    out: list[str] = []
    for i in range(0, len(sentences), per):
        chunk = "。".join(sentences[i : i + per]) + ("。" if sentences[i : i + per] else "")
        out.append(chunk)
    while len(out) < total_episodes:
        out.append(series_script)
    return out[:total_episodes]
