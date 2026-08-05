---
name: browser_pw
description: |
  Drive a real Chromium browser on the user's machine through the seven
  pw_browser_* atomic tools (open URL, read page, click, type, scroll,
  screenshot, close). Use whenever the user wants to visit / read / fill /
  screenshot a web page. Load this skill whenever the `browser-pw` toolset is
  active.
version: 1.0.0
platforms: [windows, macos, linux]
requires_toolsets: [browser-pw]
metadata:
  hermes:
    tags: [browser, web, playwright, automation]
    related_skills: [computer-use]
---

# Browser Automation (Playwright / Path B)

The `browser-pw` toolset exposes seven atomic tools that let you drive a bundled
Chromium step by step. No second LLM, no Node toolchain, no login — your own
reasoning decides which tool to call next.

## When to use

- The user asks to **open / visit / go to** a URL.
- The user wants to **read** page content, extract text, or check a page's title.
- The user wants to **fill a form**, search, or submit input.
- The user wants a **screenshot** of a web page.
- The user wants to **click** a button / link / element.

Prefer these tools over describing a page you cannot see. If you need to interact
with a site, drive it.

## The seven tools

| Tool | Purpose |
|------|---------|
| `pw_browser_navigate` | Open a URL. **Always call this first.** Returns `{url, title}`. |
| `pw_browser_snapshot` | Return the page's readable text (truncated). Use to *see* the page. |
| `pw_browser_click` | Click an element by selector. |
| `pw_browser_type` | Fill an input by selector. |
| `pw_browser_scroll` | Scroll up/down. |
| `pw_browser_screenshot` | Save a PNG, returns the path. |
| `pw_browser_close` | Close the browser for this `task_id`. Call at the end to free resources. |

## How to drive a page reliably

1. **Navigate first.** Every other call needs an open session.
2. **Snapshot before acting.** Call `pw_browser_snapshot` to read the page text,
   then pick a selector from what you actually see. Don't guess selectors blind.
3. **Selectors** (`ref` / `ref`):
   - CSS: `#id`, `.class`, `button.submit`, `input[name="q"]`
   - XPath: `//button[@id='submit']`
   - Text: `text=Sign in` (first match, substring)
   - ARIA role: `role=button` (e.g. `role=button name=Submit` not yet parsed — use plain `role=button`)
   - Placeholder: `placeholder=Search`
4. **Click / type**, then **snapshot again** to confirm the result.
5. **Screenshot** when the user wants visual proof or you need to "see" layout.
6. **Close** the session when the task is done so the browser is released.

## Concurrency

Pass a stable `task_id` per automation task. Multiple tasks with different
`task_id`s run in isolated browser sessions and never clobber each other.

## Notes / limits

- The browser is the app's **built-in Chromium**, embedded as a `<webview>` in the
  "浏览器" sidebar panel (spec §5.5, route B) — not a separate Playwright window.
  The agent drives this same visible page over CDP, so the user watches the
  operations live. No `headless` option (the panel is always shown).
- If a tool reports *"browser panel not found"*, the user must open the 浏览器
  panel first (header globe icon, or it auto-opens when you call a `pw_browser_*`
  tool). The panel only exists inside the desktop app.
- `text=` matches the first substring by default — on busy pages, snapshot first
  and prefer a precise CSS/xpath selector to avoid mis-clicks.
- Keep `task_id` stable across a single automation flow; all tasks share the one
  embedded browser panel (it is reset to blank on `pw_browser_close`).
