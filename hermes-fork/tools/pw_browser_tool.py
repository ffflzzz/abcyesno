#!/usr/bin/env python3
"""Electron-native browser automation tools (self-contained, no Playwright).

This module provides a small, dependency-light browser-automation toolset that
drives the app's *built-in* Chromium (spec §5.5, route B). The browser is not a
separate Playwright-launched process — it is the ``<webview>`` rendered inside
the app's "浏览器" sidebar panel, which is Electron's own bundled Chromium.

To drive it, the renderer reports the guest webview's ``webContentsId`` to the
Electron main process, which runs a tiny localhost-only HTTP driver service
(``PW_BROWSER_DRIVER_URL``, default http://127.0.0.1:18923). This module is a
thin HTTP client: it POSTs navigate / snapshot / click / type / scroll /
screenshot / close actions to that service, which operates the *visible*,
in-app browser natively via ``webContents.loadURL`` / ``executeJavaScript`` /
``capturePage``. No second LLM, no Node toolchain, no separately-bundled
Chromium, and crucially no Playwright ``connect_over_cdp`` (which cannot target
Electron ``<webview>`` guests). The Hermes agent's *own* LLM drives the visible
browser step by step through these atomic tools, so the user watches the
automation live, inside the app.

Requires (set by the Electron main process before spawn):
  * ``PW_BROWSER_DRIVER_URL`` — http://127.0.0.1:<driver-port>

Tools (toolset ``browser-pw``):
  pw_browser_navigate  -- open a URL
  pw_browser_snapshot  -- return the page's readable text (truncated)
  pw_browser_click     -- click an element by CSS / xpath / text
  pw_browser_type      -- fill an input
  pw_browser_scroll    -- scroll the page
  pw_browser_screenshot-- save a PNG and return its path
  pw_browser_close     -- reset the in-app browser to its idle page
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Lightweight session registry. The in-app browser is a single shared visible
# <webview>; we only track *which* task_ids have an active session so the
# snapshot/click/type/scroll/screenshot tools require a preceding navigate and
# close() can free the slot. Keyed by task_id (Hermes is single-user/desktop).
_SESSIONS: Dict[str, bool] = {}


def _emit_progress(task_id: str, message: str, level: str = "info") -> None:
    """Stream a ``browser.progress`` event to the TUI / desktop UI so the user
    sees the live state of in-app browser automation.

    Imported lazily: ``tui_gateway.server`` already imports (or could import)
    this module, so a top-level import would create a cycle. The downside is
    that progress is a best-effort affordance — if the gateway is down or the
    import path is broken, the tool still works, the panel just won't update.
    """
    if not task_id:
        return  # no session to scope the event to
    try:
        from tui_gateway.server import _emit
        _emit("browser.progress", task_id, {"message": message, "level": level})
    except Exception:
        # Progress is decorative — never let it break a tool call.
        pass


# Localhost-only HTTP client. Build a custom opener that ignores any proxy env
# (HTTP_PROXY / HTTPS_PROXY) so requests to 127.0.0.1 never get routed through a
# proxy — hermes-runner may set a proxy for upstream Agnes calls.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _driver_url() -> str:
    return os.environ.get("PW_BROWSER_DRIVER_URL", "").rstrip("/")


def _http(method: str, path: str, payload: Dict[str, Any] = None, timeout: float = 35) -> Dict[str, Any]:
    url = _driver_url() + path
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with _opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wait_ready(timeout: float = 15.0) -> None:
    """Poll the driver's /health until the in-app webview is registered.

    The user must have the 浏览器 panel open (it reports its webview id on
    dom-ready). If the panel is closed the driver answers NOT_READY and this
    raises a helpful error rather than hanging forever.
    """
    url = _driver_url()
    if not url:
        raise RuntimeError(
            "PW_BROWSER_DRIVER_URL not set — browser automation only works inside the desktop app"
        )
    deadline = time.time() + timeout
    last_err = "driver not reachable"
    while time.time() < deadline:
        try:
            health = _http("GET", "/health", timeout=5)
            if health.get("ready"):
                return
            last_err = "浏览器面板未就绪（webview 未注册）"
        except Exception as exc:  # server down / not listening yet
            last_err = str(exc)
        time.sleep(0.4)
    raise RuntimeError(
        "browser panel not ready ({}) — 浏览器面板不可用（微信会话等无面板场景常见）. "
        "DO NOT retry browser navigation — it will keep failing. Fall back to "
        "terminal(curl) to fetch page content; if直接连接超时，先检查代理环境变量 "
        "(HTTPS_PROXY/HTTP_PROXY) 是否可用再重试一次，仍失败则基于已有信息作答并"
        "明确告知用户哪部分无法访问。不要反复重试，也不要尝试 web_search / "
        "web_extract——它们不是可用工具。".format(last_err)
    )


def _require_session(task_id: str) -> None:
    _SESSIONS[task_id] = True


def check_pw_browser_requirements() -> bool:
    """Tool availability gate: the driver endpoint must be set and reachable.

    We do NOT require the webview to be registered yet — the agent may open the
    浏览器 panel itself. Requiring only the driver server to respond keeps the
    tools available whenever the desktop app is running.
    """
    url = _driver_url()
    if not url:
        return False
    try:
        _http("GET", "/health", timeout=3)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def pw_browser_navigate(url: str, task_id: str = "default", headless: bool = False) -> str:
    if not url or not str(url).strip():
        return tool_error("url is required")
    _emit_progress(task_id, f"🌐 打开 {url}", "info")
    try:
        _wait_ready()
        r = _http("POST", "/navigate", {"url": str(url).strip()}, timeout=35)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 导航失败: {exc}", "error")
        return tool_error(f"navigation failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ 导航失败: {r.get('error')}", "error")
        return tool_error(f"navigation failed: {r.get('error')}")
    _require_session(task_id)
    title = r.get("title") or ""
    _emit_progress(task_id, f"✅ 已加载 — {title or r.get('url')}", "ok")
    return tool_result({"ok": True, "url": r.get("url"), "title": title})


def pw_browser_snapshot(task_id: str = "default", max_chars: int = 8000) -> str:
    if task_id not in _SESSIONS:
        return tool_error("no active browser session; call pw_browser_navigate first")
    _emit_progress(task_id, "📄 读取页面文本…", "info")
    try:
        r = _http("POST", "/snapshot", {}, timeout=20)
    except Exception as exc:
        _emit_progress(task_id, f"❌ snapshot 失败: {exc}", "error")
        return tool_error(f"snapshot failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ snapshot 失败: {r.get('error')}", "error")
        return tool_error(f"snapshot failed: {r.get('error')}")
    text = (r.get("text") or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(truncated)"
    _emit_progress(task_id, f"✅ 拿到 {len(text)} 字符", "ok")
    return tool_result({"ok": True, "url": r.get("url"), "text": text})


def pw_browser_click(ref: str, task_id: str = "default") -> str:
    if task_id not in _SESSIONS:
        return tool_error("no active browser session; call pw_browser_navigate first")
    if not ref or not str(ref).strip():
        return tool_error("ref (selector) is required")
    _emit_progress(task_id, f"👆 点击 {ref}", "info")
    try:
        r = _http("POST", "/click", {"ref": str(ref).strip()}, timeout=15)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 点击失败: {exc}", "error")
        return tool_error(f"click failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ 点击失败: {r.get('error')}", "error")
        return tool_error(f"click failed: {r.get('error')}")
    _emit_progress(task_id, "✅ 点击完成", "ok")
    return tool_result({"ok": True, "url": r.get("url")})


def pw_browser_type(ref: str, text: str, task_id: str = "default") -> str:
    if task_id not in _SESSIONS:
        return tool_error("no active browser session; call pw_browser_navigate first")
    if not ref or not str(ref).strip():
        return tool_error("ref (selector) is required")
    preview = (text or "")[:32] + ("…" if text and len(text) > 32 else "")
    _emit_progress(task_id, f"⌨️  在 {ref} 填写 «{preview}»", "info")
    try:
        r = _http("POST", "/type", {"ref": str(ref).strip(), "text": str(text or "")}, timeout=15)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 输入失败: {exc}", "error")
        return tool_error(f"type failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ 输入失败: {r.get('error')}", "error")
        return tool_error(f"type failed: {r.get('error')}")
    _emit_progress(task_id, "✅ 输入完成", "ok")
    return tool_result({"ok": True, "url": r.get("url")})


def pw_browser_scroll(direction: str = "down", task_id: str = "default") -> str:
    if task_id not in _SESSIONS:
        return tool_error("no active browser session; call pw_browser_navigate first")
    direction = (direction or "down").strip().lower()
    _emit_progress(task_id, f"📜 滚动 {direction}", "info")
    try:
        r = _http("POST", "/scroll", {"direction": direction}, timeout=15)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 滚动失败: {exc}", "error")
        return tool_error(f"scroll failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ 滚动失败: {r.get('error')}", "error")
        return tool_error(f"scroll failed: {r.get('error')}")
    _emit_progress(task_id, "✅ 滚动完成", "ok")
    return tool_result({"ok": True, "url": r.get("url")})


def pw_browser_screenshot(task_id: str = "default") -> str:
    if task_id not in _SESSIONS:
        return tool_error("no active browser session; call pw_browser_navigate first")
    _emit_progress(task_id, "🖼️  截图…", "info")
    try:
        r = _http("POST", "/screenshot", {}, timeout=20)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 截图失败: {exc}", "error")
        return tool_error(f"screenshot failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ 截图失败: {r.get('error')}", "error")
        return tool_error(f"screenshot failed: {r.get('error')}")
    _emit_progress(task_id, f"✅ 已保存 {os.path.basename(r.get('path', ''))}", "ok")
    return tool_result({"ok": True, "path": r.get("path"), "url": r.get("url")})


def pw_browser_close(task_id: str = "default") -> str:
    _SESSIONS.pop(task_id, None)
    _emit_progress(task_id, "✖️  关闭浏览器会话", "info")
    try:
        r = _http("POST", "/close", {}, timeout=15)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 关闭失败: {exc}", "error")
        return tool_error(f"close failed: {exc}")
    if not r.get("ok"):
        _emit_progress(task_id, f"❌ 关闭失败: {r.get('error')}", "error")
        return tool_error(f"close failed: {r.get('error')}")
    _emit_progress(task_id, "✅ 已关闭", "ok")
    return tool_result({"ok": True, "closed": True})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _schema(name: str, description: str, props: Dict[str, Any], required: list) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": props,
            "required": required,
        },
    }


_PW_TOOL_SCHEMAS = [
    _schema(
        "pw_browser_navigate",
        "Open a URL in the in-app 浏览器 panel (Electron's built-in Chromium, driven natively) and return the page title+url. Start here before any other pw_browser_* call.",
        {
            "url": {"type": "string", "description": "Full URL to open, e.g. https://example.com"},
            "task_id": {"type": "string", "description": "Session id (default 'default'). Use a stable id per automation task."},
            "headless": {"type": "boolean", "description": "Ignored — the browser is always the in-app 浏览器 panel (visible)."},
        },
        ["url"],
    ),
    _schema(
        "pw_browser_snapshot",
        "Return the current page's readable text (truncated). Use after navigate/click to read page content.",
        {
            "task_id": {"type": "string", "description": "Session id used in pw_browser_navigate."},
            "max_chars": {"type": "integer", "description": "Max characters of text to return (default 8000)."},
        },
        [],
    ),
    _schema(
        "pw_browser_click",
        "Click an element. ref is a CSS selector, xpath (//...), text=..., role=..., or placeholder=...",
        {
            "ref": {"type": "string", "description": "Element selector: CSS, //xpath, text=..., role=..., placeholder=..."},
            "task_id": {"type": "string", "description": "Session id used in pw_browser_navigate."},
        },
        ["ref"],
    ),
    _schema(
        "pw_browser_type",
        "Fill an input with text. ref is a CSS selector / text=... / placeholder=...",
        {
            "ref": {"type": "string", "description": "Input selector."},
            "text": {"type": "string", "description": "Text to type into the field."},
            "task_id": {"type": "string", "description": "Session id used in pw_browser_navigate."},
        },
        ["ref", "text"],
    ),
    _schema(
        "pw_browser_scroll",
        "Scroll the page. direction is 'up' or 'down' (default down).",
        {
            "direction": {"type": "string", "description": "'up' or 'down'."},
            "task_id": {"type": "string", "description": "Session id used in pw_browser_navigate."},
        },
        [],
    ),
    _schema(
        "pw_browser_screenshot",
        "Capture the current page to a PNG and return the file path.",
        {
            "task_id": {"type": "string", "description": "Session id used in pw_browser_navigate."},
        },
        [],
    ),
    _schema(
        "pw_browser_close",
        "Reset the in-app browser for this task_id back to its idle page and free the session slot.",
        {
            "task_id": {"type": "string", "description": "Session id used in pw_browser_navigate."},
        },
        [],
    ),
]


_SCHEMA_MAP = {s["name"]: s for s in _PW_TOOL_SCHEMAS}

registry.register(
    name="pw_browser_navigate", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_navigate"],
    handler=lambda args, **kw: pw_browser_navigate(url=args.get("url", ""), task_id=kw.get("task_id") or args.get("task_id", "default"), headless=args.get("headless", False)),
    check_fn=check_pw_browser_requirements, emoji="🌐",
)
registry.register(
    name="pw_browser_snapshot", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_snapshot"],
    handler=lambda args, **kw: pw_browser_snapshot(task_id=kw.get("task_id") or args.get("task_id", "default"), max_chars=args.get("max_chars", 8000)),
    check_fn=check_pw_browser_requirements, emoji="📄",
)
registry.register(
    name="pw_browser_click", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_click"],
    handler=lambda args, **kw: pw_browser_click(ref=args.get("ref", ""), task_id=kw.get("task_id") or args.get("task_id", "default")),
    check_fn=check_pw_browser_requirements, emoji="👆",
)
registry.register(
    name="pw_browser_type", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_type"],
    handler=lambda args, **kw: pw_browser_type(ref=args.get("ref", ""), text=args.get("text", ""), task_id=kw.get("task_id") or args.get("task_id", "default")),
    check_fn=check_pw_browser_requirements, emoji="⌨️",
)
registry.register(
    name="pw_browser_scroll", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_scroll"],
    handler=lambda args, **kw: pw_browser_scroll(direction=args.get("direction", "down"), task_id=kw.get("task_id") or args.get("task_id", "default")),
    check_fn=check_pw_browser_requirements, emoji="📜",
)
registry.register(
    name="pw_browser_screenshot", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_screenshot"],
    handler=lambda args, **kw: pw_browser_screenshot(task_id=kw.get("task_id") or args.get("task_id", "default")),
    check_fn=check_pw_browser_requirements, emoji="🖼️",
)
registry.register(
    name="pw_browser_close", toolset="browser-pw", schema=_SCHEMA_MAP["pw_browser_close"],
    handler=lambda args, **kw: pw_browser_close(task_id=kw.get("task_id") or args.get("task_id", "default")),
    check_fn=check_pw_browser_requirements, emoji="✖️",
)
