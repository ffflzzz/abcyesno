import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from run_agent import AIAgent

MODELS = ["agnes-2.0-flash", "agnes-2.5-flash"]
API = "https://apihub.agnes-ai.com/v1"


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


KEY = _agnes_key()

for model in MODELS:
    fired = []
    def rcb(text, _m=model):
        fired.append(text)
    agent = AIAgent(
        model=model, max_iterations=2, provider="custom",
        base_url=API, api_key=KEY,
        quiet_mode=True, verbose_logging=False, platform="tui",
        reasoning_callback=rcb,
        enabled_toolsets=[],
    )
    try:
        resp = agent.chat("12 * 7 等于几？先简单想一想再答")
        print(f"[{model}] response: {str(resp)[:60]!r}")
    except Exception as e:
        print(f"[{model}] ERROR: {e}")
    print(f"[{model}] reasoning deltas: {len(fired)} chars: {sum(len(t) for t in fired)}")
    print()
