"""Text model wrapper for script parsing and other LLM tasks."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
DEFAULT_MODEL = os.environ.get("AGNES_TEXT_MODEL", "agnes-2.0-flash")


def _api_key() -> str:
    key = os.environ.get("AGNES_API_KEY")
    if not key:
        raise RuntimeError("AGNES_API_KEY not set")
    return key


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    response_format: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> str:
    """Call the chat completions endpoint and return the assistant's content."""
    body: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        body["response_format"] = response_format

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


async def parse_script_to_shots(script: str, *, model: str | None = None) -> list[dict]:
    """Parse a script into a list of shot dictionaries."""
    messages = [
        {"role": "system", "content": _SCRIPT_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": f"剧本：\n{script}\n\n请输出分镜 JSON 数组。"},
    ]
    content = await chat_completion(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )
    content = content.strip()
    if content.startswith("```"):
        # Strip markdown code fences if the model ignores the instruction.
        content = content.removeprefix("```json").removeprefix("```")
        content = content.removesuffix("```").strip()

    parsed = json.loads(content)
    if isinstance(parsed, dict):
        # Some models wrap the array under a key.
        for key in ("shots", "data", "result", "items"):
            if key in parsed:
                parsed = parsed[key]
                break
        else:
            # If it's a single object, wrap it.
            parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"Expected list of shots, got {type(parsed).__name__}")
    return parsed
