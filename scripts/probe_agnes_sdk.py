# -*- coding: utf-8 -*-
"""Probe: stream Agnes via the same OpenAI SDK stack Hermes uses; check reasoning_content visibility."""
import io, sys, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from openai import OpenAI


# 代理清理：本机 Clash 的系统代理会被 httpx / openai SDK 读取，agnes 请求会被
# 劫走（TLS 中断 → APIConnectionError）。策略与 dashboard/pipeline/config.py 一致。
for _k in list(os.environ.keys()):
    if "proxy" in _k.lower():
        del os.environ[_k]
os.environ["no_proxy"] = "*"


def _agnes_key() -> str:
    """从 HERMES_HOME/.env 读 AGNES_API_KEY。

    不再硬编码：key 会轮换（cpk- 那把已废弃、报了 402 subscription_not_found），
    写死只会让脚本悄悄失效。
    """
    # 不能只信 HERMES_HOME：宿主环境可能把它指向非便携目录（实测
    # AppData\Local\hermes），那里的 .env 没有这个变量，盲信会静默拿到空 key，
    # 表现为 401 "Token not provided"。两个位置都试，取第一个非空值。
    homes = []
    if os.environ.get("HERMES_HOME"):
        homes.append(os.environ["HERMES_HOME"])
    homes.append(os.path.expanduser("~/.hermes_portable_data"))
    for home in homes:
        try:
            with open(os.path.join(home, ".env"), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("AGNES_API_KEY="):
                        k = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if k:
                            return k
        except OSError:
            continue
    return os.environ.get("AGNES_API_KEY", "")


client = OpenAI(
    api_key=_agnes_key(),
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
