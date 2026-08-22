#!/usr/bin/env python3
"""paper_rewriter_agent -- 论文重写 ReAct Agent（LangGraph）。

学术论文 → 中文通俗重写：搜索/下载论文(arXiv/S2/CrossRef/PubMed) → 大纲(HITL 确认)
→ 逐章写作(每章 HITL 确认+独立审查) → 导出 PDF。

子模块统一加 pr_ 前缀（绝对导入），避免与同进程其它 agent 的顶层模块名碰撞。
LLM 凭证走 Agnes 运行时解析（env > config.yaml），不硬编码 key。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict

# Make the package directory importable so ``pr_*`` submodules resolve with the
# same absolute names the runtime uses（manjucraft 同款约定）。
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

import pr_graph  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

DEFAULT_AUDIENCE = "大一非理工科学生"

# ReAct 长循环上限：默认 25 步远远不够逐章写作，经 runtime 注入 config。
RECURSION_LIMIT = int(os.getenv("PR_RECURSION_LIMIT", "400"))

# Canonical stage map（contract L5）：进度条按 node 命中顺序推进。
WORKFLOW_STAGES = [
    ("agent", "智能体推理"),
    ("tools", "工具执行"),
    ("review", "质量审查"),
]


def _new_run_id() -> str:
    return f"pr-{datetime.now():%Y%m%d-%H%M%S}"


def _kickoff_message(paper_title: str, original_chars: int,
                     audience: str) -> str:
    title_part = f"《{paper_title}》" if paper_title else ""
    return (
        f"请开始重写论文{title_part}。目标读者：{audience}。"
        f"原文长度：{original_chars}字。"
        f"先浏览原文结构，然后生成大纲。"
    )


def build_initial_state(input_text: str) -> Dict[str, Any]:
    """Map the runtime's free-text input to the graph input (MessagesState).

    自由文本二义性启发式：
    - 长文本（>2000 字符）视为论文原文，落盘 original.txt 并发起重写；
    - 短文本视为对 agent 的指令（例如"帮我下载 arXiv:2401.xxxxx"），
      不落盘原文，由 agent 通过 download_paper / search_paper 自行获取。
    """
    run_id = _new_run_id()
    text = str(input_text or "").strip()
    is_original = len(text) > 2000

    pr_graph.set_current_run_id(run_id)
    pr_graph.init_run(run_id, text if is_original else "")

    first_message = (
        _kickoff_message("未命名论文", len(text), DEFAULT_AUDIENCE)
        if is_original else (text or "你好")
    )
    return {"messages": [HumanMessage(content=first_message)]}


def build_initial_state_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Map a structured contract input (L2) to the graph input."""
    # 容错：自由文本误入结构化通道时退回 build_initial_state。
    if not isinstance(obj, dict):
        return build_initial_state(str(obj or ""))

    paper_title = str(obj.get("paper_title") or "").strip()
    original_text = str(obj.get("original_text") or "").strip()
    audience = str(obj.get("target_audience") or "").strip() or DEFAULT_AUDIENCE
    instruction = str(obj.get("instruction") or "").strip()

    run_id = _new_run_id()
    pr_graph.set_current_run_id(run_id)
    pr_graph.init_run(run_id, original_text, paper_title=paper_title)

    if instruction:
        first_message = instruction
    elif original_text or paper_title:
        first_message = _kickoff_message(paper_title, len(original_text), audience)
    else:
        first_message = "你好"

    return {"messages": [HumanMessage(content=first_message)]}


def summarize_state(result: Dict[str, Any]) -> Dict[str, Any]:
    """Typed summary + artifacts（contract L3/L5）。

    图的终态是 MessagesState（只有 messages），产物都在磁盘上 —— 从当前
    run 目录读取 progress.json / outline.txt / output.pdf 汇总。
    """
    artifacts: list[Dict[str, Any]] = []
    parts: list[str] = []

    run_id = pr_graph.get_current_run_id()
    run_dir = os.path.join(pr_graph._RUNS_DIR, run_id) if run_id else ""

    chapters: Dict[str, Any] = {}
    outline_chars = 0
    pdf_path = ""
    if run_dir and os.path.isdir(run_dir):
        progress_path = os.path.join(run_dir, "progress.json")
        if os.path.exists(progress_path):
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    chapters = json.load(f).get("chapters", {}) or {}
            except Exception:
                chapters = {}
        outline_path = os.path.join(run_dir, "outline.txt")
        if os.path.exists(outline_path):
            try:
                outline_chars = os.path.getsize(outline_path)
            except OSError:
                outline_chars = 0
        pdf_path = os.path.join(run_dir, "output.pdf")

    total_chars = sum(int(c.get("chars", 0)) for c in chapters.values() if isinstance(c, dict))
    parts.append(f"status={'done' if chapters else 'idle'}")
    parts.append(f"chapters={len(chapters)}")
    parts.append(f"total_chars={total_chars}")
    if outline_chars:
        parts.append(f"outline_chars={outline_chars}")

    if pdf_path and os.path.exists(pdf_path):
        parts.append(f"pdf={pdf_path}")
        artifacts.append({"id": "paper_pdf", "type": "file", "mime": "application/pdf",
                          "source": "path", "path": pdf_path, "label": "重写PDF"})
    for ch_id in sorted(chapters.keys()):
        ch_path = os.path.join(run_dir, "chapters", f"{ch_id}.txt")
        if os.path.exists(ch_path):
            artifacts.append({"id": f"chapter_{ch_id}", "type": "text", "source": "path",
                              "path": ch_path, "label": f"章节·{ch_id}"})
    if not artifacts:
        last = (result or {}).get("messages", [None])[-1] if (result or {}).get("messages") else None
        reply = getattr(last, "content", "") if last is not None else ""
        if reply:
            artifacts.append({"id": "reply", "type": "text", "source": "text",
                              "text": str(reply)[:4000], "label": "回复"})
    return {"summary": "; ".join(parts), "artifacts": artifacts}


# Compile with a checkpointer so LangGraph interrupt() / human-in-the-loop
# approval gates work（与 manjucraft 相同约定）。
graph = pr_graph.build_graph(checkpointer=InMemorySaver())
