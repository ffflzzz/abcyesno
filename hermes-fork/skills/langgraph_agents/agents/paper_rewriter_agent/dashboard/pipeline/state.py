"""论文重写 LangGraph State 定义"""
from __future__ import annotations
from typing import TypedDict, Annotated, Literal


class RewriteState(TypedDict, total=False):
    """LangGraph 共享状态"""

    # === 输入 ===
    paper_title: str              # 论文标题
    original_text: str            # 原文全文
    target_audience: str          # 目标读者描述
    language: str                 # 输出语言，默认 "zh"

    # === 流程控制 ===
    current_phase: str            # 当前阶段名
    round_num: int                # 当前轮次（reviewer→judge循环计数）
    max_rounds: int               # 最大轮次，默认 3
    error: str                    # 错误信息

    # === 大纲 ===
    outline: str                  # 章节大纲（Markdown）

    # === 内容 ===
    chapters: dict[str, str]      # {章节编号: 章节内容}
    chapter_order: list[str]      # 章节顺序 [ch1, ch2, ...]
    current_chapter_idx: int      # 当前写到第几章
    full_rewrite: str             # 合并后的完整重写

    # === 审查 ===
    review_report: str            # 比对员审查报告
    score: float                  # 评分 (0-10)
    fact_check_report: str        # 事实核查报告
    factual_accuracy: float       # 事实准确度 (0-10)
    judge_verdict: str            # "PASS" or "FAIL"
    fix_list: list[str]           # 裁判给出的修改清单

    # === 最终输出 ===
    pdf_path: str                 # 生成的PDF路径
    status: str                   # pipeline整体状态

    # === AG-UI 兼容 ===
    messages: list                # LangChain 消息列表（AG-UI 协议要求）
