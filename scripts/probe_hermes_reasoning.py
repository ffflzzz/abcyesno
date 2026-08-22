# -*- coding: utf-8 -*-
"""Probe: build AIAgent like tui_gateway does, run one turn, print which callbacks fire."""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes_portable_data"))

from run_agent import AIAgent

fired = {"reasoning": [], "thinking": [], "stream": []}

def reasoning_cb(text):
    fired["reasoning"].append(text)

def thinking_cb(text):
    fired["thinking"].append(text)

agent = AIAgent(
    model="agnes-2.5-flash",
    max_iterations=3,
    provider="custom",
    base_url="https://apihub.agnes-ai.com/v1",
    api_key="cpk-VdOissJMrHBFsSi193GP7mxpLnwCqYW2hr9ybTqxXq9KDpno",
    quiet_mode=True,
    verbose_logging=False,
    platform="tui",
    reasoning_callback=reasoning_cb,
    thinking_callback=thinking_cb,
    enabled_toolsets=[],  # no tools -> isolate
)

resp = agent.chat("12 * 7 等于几？先想一想再答")
print("=== response:", str(resp)[:200])
print("=== reasoning delta count:", len(fired["reasoning"]), "total chars:", sum(len(t) for t in fired["reasoning"]))
if fired["reasoning"]:
    print("=== reasoning head:", "".join(fired["reasoning"])[:150])
print("=== thinking cb count:", len(fired["thinking"]))
