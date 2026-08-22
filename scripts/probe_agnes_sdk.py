# -*- coding: utf-8 -*-
"""Probe: stream Agnes via the same OpenAI SDK stack Hermes uses; check reasoning_content visibility."""
import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from openai import OpenAI

client = OpenAI(
    api_key="cpk-VdOissJMrHBFsSi193GP7mxpLnwCqYW2hr9ybTqxXq9KDpno",
    base_url="https://apihub.agnes-ai.com/v1",
)

stream = client.chat.completions.create(
    model="agnes-2.5-flash",
    messages=[{"role": "user", "content": "12 * 7 等于几？先想一想再答"}],
    stream=True,
    max_tokens=2000,
)

reasoning = ""
content = ""
first_delta_attrs = None
for chunk in stream:
    if not getattr(chunk, "choices", None):
        continue
    delta = chunk.choices[0].delta
    if first_delta_attrs is None:
        d = delta.model_dump() if hasattr(delta, "model_dump") else {}
        first_delta_attrs = sorted(d.keys())
        print("delta keys on first chunk:", first_delta_attrs)
        print("has model_extra:", hasattr(delta, "model_extra"), type(getattr(delta, "model_extra", None)))
    rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
    if rc:
        reasoning += rc
    if delta and delta.content:
        content += delta.content

print("reasoning len:", len(reasoning))
print("content len:", len(content))
print("reasoning head:", reasoning[:150])
