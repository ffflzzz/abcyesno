#!/usr/bin/env python3
"""Playwright-backed browser automation tools (self-contained, no agent-browser).

This module provides a small, dependency-light browser-automation toolset that
drives the app's *built-in* Chromium (spec §5.5, route B). The browser is not a
separate Playwright-launched process — it is the ``<webview>`` rendered inside the
app's "浏览器" sidebar panel, which is Electron's own bundled Chromium.

To drive it, the Python side connects to Electron over the DevTools Protocol
(``playwright.sync_api.chromium.connect_over_cdp(PW_CDP_URL)``) and selects the
embedded webview by its marker URL (``PW_WEBVIEW_MARKER``). No second LLM, no Node
toolchain, no separately-bundled Chromium. The Hermes agent's *own* LLM drives the
visible, in-app browser step by step through these atomic tools.

Requires (set by the Electron main process before spawn):
  * ``PW_CDP_URL``  — http://127.0.0.1:<remote-debugging-port>
  * ``PW_WEBVIEW_MARKER`` — the data: URL the sidebar webview loads as its sentinel

Tools (toolset ``browser-pw``):
  pw_browser_navigate  -- open a URL
  pw_browser_snapshot  -- return the page's readable text (truncated)
  pw_browser_click     -- click an element by CSS / xpath / text
  pw_browser_type      -- fill an input
  pw_browser_scroll    -- scroll the page
  pw_browser_screenshot-- save a PNG and return its path
  pw_browser_close     -- tear down the session's browser
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Per-task_id browser state. Hermes is single-user/desktop, but we still key by
# task_id so concurrent automation tasks don't clobber each other's page.
_STATE_LOCK = threading.Lock()
_SESSIONS: Dict[str, _Session] = {}


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


# Shared CDP connection to Electron's built-in Chromium. A single embedded
# <webview> panel exists in the app; all task_ids drive that same visible page.
_DEFAULT_MARKER = "data:text/html,<title>browser-pw-marker</title>"
_PW = None          # playwright sync runner
_BROWSER = None     # connected Browser (Electron, via CDP)
_PAGE = None        # the embedded webview Page


def _is_main_window(url: str) -> bool:
    """Heuristic: the app's own BrowserWindow loads index.html (file:// or dev
    server). The embedded <webview> loads the marker data: URL or a user URL."""
    u = url or ""
    return ("index.html" in u) or (u.startswith("file://") and "index" in u)


def _find_webview_page(browser, marker: str, timeout: float = 12.0):
    """Scan CDP targets for the embedded webview (identified by its marker URL).

    Falls back to the first non-main-window page when the marker URL is gone
    (e.g. the user manually navigated away) so agent control still works.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        candidates = []
        for ctx in browser.contexts:
            for page in ctx.pages:
                try:
                    url = page.url or ""
                except Exception:
                    url = ""
                if url == marker or "browser-pw-marker" in url:
                    return page
                if not _is_main_window(url) and url:
                    candidates.append(page)
        if candidates:
            # Prefer a fresh marker (data: URL), else the first non-main page.
            for p in candidates:
                try:
                    if (p.url or "").startswith("data:"):
                        return p
                except Exception:
                    pass
            return candidates[0]
        time.sleep(0.3)
    raise RuntimeError(
        "browser panel not found — open the 浏览器 panel in the app, then retry"
    )


def _acquire_page():
    """Connect to Electron over CDP (once) and return the embedded webview page."""
    global _PW, _BROWSER, _PAGE
    with _STATE_LOCK:
        if _PAGE is not None and not _PAGE.is_closed():
            return _PAGE
        # Stale connection — drop it before reconnecting.
        if _PW is not None:
            try:
                _PW.stop()
            except Exception:
                pass
        _PW = _BROWSER = _PAGE = None
    cdp_url = os.environ.get("PW_CDP_URL")
    if not cdp_url:
        raise RuntimeError(
            "PW_CDP_URL not set — browser automation only works inside the desktop app"
        )
    marker = os.environ.get("PW_WEBVIEW_MARKER") or _DEFAULT_MARKER
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        try:
            pw.stop()
        except Exception:
            pass
        raise RuntimeError(f"cannot connect to Electron CDP at {cdp_url}: {exc}")
    page = _find_webview_page(browser, marker)
    with _STATE_LOCK:
        _PW, _BROWSER, _PAGE = pw, browser, page
    return page


def check_pw_browser_requirements() -> bool:
    """Tool availability gate: Playwright importable + Electron CDP endpoint set.

    The browser is Electron's own Chromium, so no separate Chromium build is
    required (unlike the old launched-process design).
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return False
    return bool(os.environ.get("PW_CDP_URL"))


class _Session:
    """Wraps the single embedded webview page (shared across task_ids)."""

    def __init__(self, page) -> None:
        self.page = page

    def close(self) -> None:
        # The browser is the app's embedded panel — don't tear it down. Just
        # reset it to the marker so the next automation starts from a blank page.
        try:
            if self.page and not self.page.is_closed():
                marker = os.environ.get("PW_WEBVIEW_MARKER") or _DEFAULT_MARKER
                self.page.goto(marker, timeout=5000, wait_until="domcontentloaded")
        except Exception:
            pass


def _get_session(task_id: str, headless: bool = False) -> _Session:
    page = _acquire_page()
    with _STATE_LOCK:
        sess = _SESSIONS.get(task_id)
        if sess is None:
            sess = _Session(page)
            _SESSIONS[task_id] = sess
        return sess


def _resolve(locator_str: str, task_id: str = "default"):
    """Turn a CSS / xpath / text= selector string into a Playwright locator.

    The session is looked up by the explicit ``task_id`` (passed through from the
    handler) rather than a module-level global, so concurrent automation tasks
    can't resolve the wrong page.
    """
    sess = _SESSIONS.get(task_id)
    if sess is None:
        raise RuntimeError("no active browser session")
    page = sess.page
    s = (locator_str or "").strip()
    if s.startswith("//") or s.startswith("(/"):
        return page.locator(f"xpath={s}")
    if s.startswith("text="):
        return page.get_by_text(s[len("text="):], exact=False).first
    if s.startswith("role="):
        return page.get_by_role(s[len("role="):])
    if s.startswith("placeholder="):
        return page.get_by_placeholder(s[len("placeholder="):])
    return page.locator(s)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def pw_browser_navigate(url: str, task_id: str = "default", headless: bool = False) -> str:
    if not url or not str(url).strip():
        return tool_error("url is required")
    _emit_progress(task_id, f"🌐 打开 {url}", "info")
    sess = _get_session(task_id, bool(headless))
    try:
        sess.page.goto(str(url).strip(), wait_until="load", timeout=30000)
    except Exception as exc:  # navigation timeout / bad URL
        _emit_progress(task_id, f"❌ 导航失败: {exc}", "error")
        return tool_error(f"navigation failed: {exc}")
    title = sess.page.title()
    _emit_progress(task_id, f"✅ 已加载 — {title or sess.page.url}", "ok")
    return tool_result({
        "ok": True,
        "url": sess.page.url,
        "title": title,
    })


def pw_browser_snapshot(task_id: str = "default", max_chars: int = 8000) -> str:
    sess = _SESSIONS.get(task_id)
    if sess is None:
        return tool_error("no active browser session; call pw_browser_navigate first")
    _emit_progress(task_id, "📄 读取页面文本…", "info")
    try:
        text = sess.page.inner_text("body") or ""
    except Exception as exc:
        _emit_progress(task_id, f"❌ snapshot 失败: {exc}", "error")
        return tool_error(f"snapshot failed: {exc}")
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(truncated)"
    _emit_progress(task_id, f"✅ 拿到 {len(text)} 字符", "ok")
    return tool_result({"ok": True, "url": sess.page.url, "text": text})


def pw_browser_click(ref: str, task_id: str = "default") -> str:
    sess = _SESSIONS.get(task_id)
    if sess is None:
        return tool_error("no active browser session; call pw_browser_navigate first")
    if not ref or not str(ref).strip():
        return tool_error("ref (selector) is required")
    _emit_progress(task_id, f"👆 点击 {ref}", "info")
    try:
        _resolve(str(ref).strip(), task_id).first.click(timeout=10000)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 点击失败: {exc}", "error")
        return tool_error(f"click failed: {exc}")
    _emit_progress(task_id, "✅ 点击完成", "ok")
    return tool_result({"ok": True, "url": sess.page.url})


def pw_browser_type(ref: str, text: str, task_id: str = "default") -> str:
    sess = _SESSIONS.get(task_id)
    if sess is None:
        return tool_error("no active browser session; call pw_browser_navigate first")
    if not ref or not str(ref).strip():
        return tool_error("ref (selector) is required")
    preview = (text or "")[:32] + ("…" if text and len(text) > 32 else "")
    _emit_progress(task_id, f"⌨️  在 {ref} 填写 «{preview}»", "info")
    try:
        _resolve(str(ref).strip(), task_id).first.fill(str(text or ""), timeout=10000)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 输入失败: {exc}", "error")
        return tool_error(f"type failed: {exc}")
    _emit_progress(task_id, "✅ 输入完成", "ok")
    return tool_result({"ok": True, "url": sess.page.url})


def pw_browser_scroll(direction: str = "down", task_id: str = "default") -> str:
    sess = _SESSIONS.get(task_id)
    if sess is None:
        return tool_error("no active browser session; call pw_browser_navigate first")
    direction = (direction or "down").strip().lower()
    _emit_progress(task_id, f"📜 滚动 {direction}", "info")
    try:
        delta = -800 if direction == "up" else 800
        sess.page.mouse.wheel(0, delta)
    except Exception as exc:
        _emit_progress(task_id, f"❌ 滚动失败: {exc}", "error")
        return tool_error(f"scroll failed: {exc}")
    _emit_progress(task_id, "✅ 滚动完成", "ok")
    return tool_result({"ok": True, "url": sess.page.url})


def pw_browser_screenshot(task_id: str = "default") -> str:
    sess = _SESSIONS.get(task_id)
    if sess is None:
        return tool_error("no active browser session; call pw_browser_navigate first")
    _emit_progress(task_id, "🖼️  截图…", "info")
    try:
        out_dir = Path(os.environ.get("HERMES_HOME", tempfile.gettempdir())) / "browser-shots"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"shot-{task_id}-{int(__import__('time').time())}.png"
        sess.page.screenshot(path=str(path))
    except Exception as exc:
        _emit_progress(task_id, f"❌ 截图失败: {exc}", "error")
        return tool_error(f"screenshot failed: {exc}")
    _emit_progress(task_id, f"✅ 已保存 {path.name}", "ok")
    return tool_result({"ok": True, "path": str(path), "url": sess.page.url})


def pw_browser_close(task_id: str = "default") -> str:
    with _STATE_LOCK:
        sess = _SESSIONS.pop(task_id, None)
    if sess is None:
        return tool_result({"ok": True, "closed": False, "note": "no active session"})
    _emit_progress(task_id, "✖️  关闭浏览器会话", "info")
    try:
        sess.close()
    except Exception as exc:
        _emit_progress(task_id, f"❌ 关闭失败: {exc}", "error")
        return tool_error(f"close failed: {exc}")
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
        "Open a URL in the in-app 浏览器 panel (Electron's built-in Chromium) and return the page title+url. Start here before any other pw_browser_* call.",
        {
            "url": {"type": "string", "description": "Full URL to open, e.g. https://example.com"},
            "task_id": {"type": "string", "description": "Session id (default 'default'). Use a stable id per automation task."},
            "headless": {"type": "boolean", "description": "Ignored in embedded mode — the browser is always the in-app 浏览器 panel (visible)."},
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
        "Close the automation browser for this task_id and free resources.",
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
