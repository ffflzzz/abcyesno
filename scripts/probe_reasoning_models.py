import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from run_agent import AIAgent

MODELS = ["agnes-2.0-flash", "agnes-2.5-flash"]
API = "https://apihub.agnes-ai.com/v1"
KEY = "cpk-VdOissJMrHBFsSi193GP7mxpLnwCqYW2hr9ybTqxXq9KDpno"

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
