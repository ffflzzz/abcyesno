"""LangGraph StateGraph 定义 — 论文重写多Agent流水线

图结构:
  outline_generator → writer → reviewer → fact_checker → judge
                                                ├── PASS → pdf_generator → END
                                                └── FAIL → writer (循环)
"""
from __future__ import annotations
from langgraph.graph import StateGraph, END
from .state import RewriteState
from .nodes import (
    outline_generator,
    writer,
    reviewer,
    fact_checker,
    judge,
    pdf_generator,
    should_continue_writing,
)


def build_graph() -> StateGraph:
    """构建并返回论文重写 StateGraph"""

    graph = StateGraph(RewriteState)

    # 添加节点
    graph.add_node("outline_generator", outline_generator)
    graph.add_node("writer", writer)
    graph.add_node("reviewer", reviewer)
    graph.add_node("fact_checker", fact_checker)
    graph.add_node("judge", judge)
    graph.add_node("pdf_generator", pdf_generator)

    # 入口
    graph.set_entry_point("outline_generator")

    # 固定边
    graph.add_edge("outline_generator", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_edge("reviewer", "fact_checker")
    graph.add_edge("fact_checker", "judge")

    # 条件边：judge → PASS:pdf / FAIL:writer
    graph.add_conditional_edges(
        "judge",
        should_continue_writing,
        {
            "pdf_generator": "pdf_generator",
            "writer": "writer",
        },
    )

    # 终点
    graph.add_edge("pdf_generator", END)

    return graph


def compile_graph():
    """编译graph，返回可执行的app"""
    graph = build_graph()
    return graph.compile()
