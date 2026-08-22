"""paper_rewriter_agent 配置 — LLM 凭证与运行目录。

设计要点（对齐项目约定，勿硬编码）：
- LLM 凭证优先级：AGNES_API_KEY / AGNES_BASE_URL / AGNES_MODEL 环境变量
  > Hermes config.yaml providers.custom 块（经 langgraph_runtime._get_agnes_credentials）。
  不再使用独立项目里硬编码的 cpk- key。
- 运行产物目录统一收敛到 HERMES_HOME（默认 ~/.hermes_portable_data）下的
  paper_rewriter_runs/<run_id>/，随包代码目录保持只读。
"""

from __future__ import annotations

import os
from pathlib import Path


def _hermes_home() -> Path:
    base = os.environ.get("HERMES_HOME") or str(
        Path.home() / ".hermes_portable_data"
    )
    return Path(base)


# ── 运行目录 ─────────────────────────────────────────────────────────────
RUNS_DIR = os.environ.get("PR_RUNS_DIR") or str(_hermes_home() / "paper_rewriter_runs")

# ── 质量阈值 / 轮次 ─────────────────────────────────────────────────────
PASS_SCORE = float(os.getenv("PASS_SCORE", "8.5"))
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3"))

# agent 单步输出上限（章节内容以 tool_call 参数传输，需要大空间）
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "16384"))

# ReAct 循环上限（agent.py 以 RECURSION_LIMIT 暴露给 runtime 注入 config）
RECURSION_LIMIT = int(os.getenv("PR_RECURSION_LIMIT", "400"))


def get_llm_credentials() -> tuple[str, str, str]:
    """Return (base_url, api_key, model)。

    Env (AGNES_*) 优先；否则读 Hermes 运行时凭证解析器（config.yaml）。
    """
    from langgraph_agents.langgraph_runtime import _get_agnes_credentials

    base_url, api_key, model = _get_agnes_credentials()

    # Per-agent model override without touching the global config.
    override = os.environ.get("PR_LLM_MODEL", "").strip()
    if override:
        model = override
    return base_url, api_key, model
