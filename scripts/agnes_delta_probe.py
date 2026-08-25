# -*- coding: utf-8 -*-
"""
agnes_delta_probe.py — 验证 Agnes 原始流式 delta 语义（增量 vs 累积/重复）

目的：确认 agui-server 收到的 message.delta 到底是不是纯增量。
本脚本绕过 Hermes，直接调 Agnes 的 OpenAI 兼容 /chat/completions 流式接口，
逐 chunk 打印 delta.content（repr），观察：
  1. delta 是逐字符增量（incremental）还是累积（cumulative）？
  2. 数字/连续重复字符（"11"、"2026"）是怎么分片的？
  3. reason 模型是否有 reasoning_content 字段？

用法：python scripts/agnes_delta_probe.py
（api_key 从 ~/.hermes_portable_data/config.yaml 读取，不打印。）
"""
import json
import os
import re
import urllib.request

CONFIG = os.path.expanduser("~/.hermes_portable_data/config.yaml")
PROMPT = os.environ.get("AGNES_PROBE_PROMPT", "明天是几号？今天是几号？")


def load_api_key():
    raw = open(CONFIG, encoding="utf-8").read()
    m = re.search(r"api_key:\s*[\"']?([^\"'\n]+)", raw)
    if not m:
        raise SystemExit("api_key not found in config.yaml")
    key = m.group(1).strip().strip('"').strip("'")
    return key


def stream():
    key = load_api_key()
    body = {
        "model": "agnes-2.0-flash",
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
    }
    req = urllib.request.Request(
        "https://apihub.agnes-ai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=120)
    buf = b""
    idx = 0
    for raw in resp:
        buf += raw
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line or not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                return
            try:
                obj = json.loads(data)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if content is not None:
                idx += 1
                print(f"[{idx:3d}] content={content!r}")
            if reasoning is not None and reasoning != "":
                print(f"      reasoning={reasoning!r}")


if __name__ == "__main__":
    stream()
