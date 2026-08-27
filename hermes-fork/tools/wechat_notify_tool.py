#!/usr/bin/env python3
"""
wechat_notify Tool — agent 主动给已绑定的微信用户发通知（走微信桥）。

为什么是工具而不是让 agent 用 curl（2026-08-27 乱码根因）：
  Windows cmd 是 GBK 代码页，命令行内联中文 JSON 会被转码绞碎——
  实测 "现在时间是2026年8月27日" → "◆◆◆◆◆2026◆8◆◆27◆◆◆◆"。
  工具参数走 function-calling 的 JSON 通道（全程 UTF-8），
  彻底绕开 cmd 编码问题。

链路：本工具 → agui-server POST /api/wechat/notify → wechat-bridge
  sendTestMessage → iLink 被动回复窗口 → 绑定用户的微信。

限制：iLink 只允许在用户近期给机器人发过消息后的回复窗口内主动发送；
  失败时桥返回 error，本工具原样透传给模型（不要掩盖，如实告知用户）。
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 2000


def wechat_notify(text: Optional[str] = None) -> str:
    from tools.registry import tool_error, tool_result

    if not isinstance(text, str) or not text.strip():
        return tool_error("text 不能为空")
    text = text.strip()[:_MAX_TEXT_CHARS]

    import urllib.request

    port = os.environ.get("AGUI_PORT") or "9121"
    url = f"http://127.0.0.1:{port}/api/wechat/notify"
    try:
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return tool_error(f"调用微信通知端点失败：{exc}")

    # 桥返回 {ok, error?}；ok=false 时把 error 原样带给模型（iLink 回复窗口
    # 过期、桥未连接等都从这里透出），由模型如实转告用户。
    if data.get("ok"):
        return tool_result(success=True, note="已通过微信桥发送给绑定的微信用户")
    return tool_error(f"微信桥发送失败：{data.get('error') or '未知原因'}")


def check_wechat_notify_requirements() -> bool:
    """无外部依赖——agui-server 不在时调用会得到明确错误，不妨碍暴露工具。"""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================
WECHAT_NOTIFY_SCHEMA = {
    "name": "wechat_notify",
    "description": (
        "给用户已绑定的微信发送一条通知消息（用户说「微信通知我/完成后发微信」时使用）。\n"
        "注意：\n"
        "- 这是唯一的微信发送方式——不要用 curl/终端发微信通知，"
        "cmd 命令行会把中文绞成乱码。\n"
        "- 发送依赖微信桥在线且用户近期给机器人发过消息（微信被动回复窗口限制）；"
        "失败时错误信息会原样返回，请如实告知用户（例如提示先给机器人发条消息）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要发送的通知内容（纯文本，最长 2000 字符）",
            },
        },
        "required": ["text"],
    },
}


# --- Registry ---
from tools.registry import registry  # noqa: E402

registry.register(
    name="wechat_notify",
    toolset="messaging",
    schema=WECHAT_NOTIFY_SCHEMA,
    handler=lambda args, **kw: wechat_notify(text=args.get("text")),
    check_fn=check_wechat_notify_requirements,
    emoji="💬",
)
