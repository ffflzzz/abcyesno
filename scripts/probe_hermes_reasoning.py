# -*- coding: utf-8 -*-
"""Probe: build AIAgent like tui_gateway does, run one turn, print which callbacks fire."""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes_portable_data"))


# 代理清理：Clash 系统代理会被 httpx 读取，agnes 请求被劫（TLS 中断）。
for _k in list(os.environ.keys()):
    if "proxy" in _k.lower():
        del os.environ[_k]
os.environ["no_proxy"] = "*"


def _agnes_key() -> str:
    """从 HERMES_HOME/.env 读 AGNES_API_KEY（不再硬编码：key 会轮换）。"""
    # 不能只信 HERMES_HOME：宿主环境可能把它指向非便携目录（实测
    # AppData\Local\hermes），那里的 .env 没有这个变量，盲信会静默拿到空 key。
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
    api_key=_agnes_key(),
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
