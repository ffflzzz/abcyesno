"""AG-UI Agent 包装器 — 将 LangGraph graph 暴露为 AG-UI 协议

CopilotKit SDK 版本:
  copilotkit==0.1.94
  ag-ui-langgraph==0.0.41
  ag-ui-protocol==0.1.19

LangGraphAGUIAgent 继承 LangGraphAgent (有 run 方法)，
但 CopilotKitRemoteEndpoint 需要 Agent 子类 (有 execute 方法)。
此模块提供桥接包装。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, List, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from copilotkit import CopilotKitRemoteEndpoint, LangGraphAGUIAgent
from copilotkit.integrations.fastapi import add_fastapi_endpoint
from ag_ui_langgraph.agent import RunAgentInput
from ag_ui.core.types import UserMessage

from pipeline.nodes import (
    outline_generator,
    writer,
    reviewer,
    fact_checker,
    judge,
    pdf_generator,
    should_continue_writing,
)

from pipeline.state import RewriteState
from pipeline.graph import build_graph


# ─── 桥接包装器 ───

class PaperRewriterAgent:
    """包装 LangGraphAGUIAgent，桥接 execute → run 接口。"""

    def __init__(self, agui_agent: LangGraphAGUIAgent):
        self._agent = agui_agent
        self.name = agui_agent.name
        self.description = agui_agent.description

    def dict_repr(self):
        return {"name": self.name, "description": self.description or "", "type": "langgraph_agui"}

    def execute(
        self,
        *,
        thread_id: str,
        node_name: str = None,
        state: dict = None,
        config: dict = None,
        messages: list = None,
        actions: list = None,
        meta_events: list = None,
        **kwargs,
    ):
        """桥接 execute → run：将参数打包为 RunAgentInput"""
        # 转换 CopilotKit Message dicts 为 AG-UI UserMessage 对象
        agui_messages = []
        for msg in (messages or []):
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                if role == "user":
                    agui_messages.append(UserMessage(
                        id=msg.get("id", str(uuid.uuid4())),
                        role="user",
                        content=msg.get("content", ""),
                    ))
                # assistant/tool messages 跳过，pipeline 不需要历史
            else:
                agui_messages.append(msg)

        run_input = RunAgentInput(
            thread_id=thread_id or str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            state=state or {},
            messages=agui_messages,
            tools=[],
            context=[],
            forwarded_props={"nodeName": node_name} if node_name else {},
        )

        async def _stream():
            async for event in self._agent.run(run_input):
                # StreamingResponse 需要字符串，AG-UI event 是 Pydantic 对象
                if isinstance(event, str):
                    yield event
                else:
                    yield event.model_dump_json() + "\n"

        return _stream()


# ─── 构建 AG-UI 兼容的图 ───

def build_agui_graph():
    """构建 AG-UI 兼容的图。

    在原图基础上:
    1. 状态中包含 messages 字段（AG-UI 协议要求）
    2. 使用 MemorySaver checkpointer（支持 thread 管理）
    """
    from pipeline.nodes import (
        outline_generator,
        writer,
        reviewer,
        judge,
        pdf_generator,
        should_continue_writing,
    )

    graph = StateGraph(RewriteState)

    # 入口：解析用户输入
    def parse_input(state: RewriteState) -> dict:
        """从 AG-UI messages 中提取论文参数，初始化 pipeline 输入"""
        messages = state.get("messages", [])
        # 如果已经有 original_text，说明参数已设置，跳过
        if state.get("original_text"):
            return {}

        # 从最后一条用户消息中提取
        user_text = ""
        for msg in reversed(messages):
            if hasattr(msg, "content"):
                user_text = msg.content
                break
            elif isinstance(msg, dict):
                user_text = msg.get("content", "")
                if user_text:
                    break

        if not user_text:
            user_text = str(messages[-1]) if messages else "未提供内容"

        # 简单解析：用换行分隔，第一行当标题，其余当原文
        lines = user_text.strip().split("\n", 1)
        paper_title = lines[0].strip() if lines else "未命名论文"
        original_text = lines[1].strip() if len(lines) > 1 else lines[0].strip()

        return {
            "paper_title": paper_title,
            "original_text": original_text,
            "target_audience": state.get("target_audience", "大一非理工科学生"),
            "language": state.get("language", "zh"),
            "max_rounds": state.get("max_rounds", 3),
            "current_phase": "init",
            "round_num": 1,
            "chapters": {},
            "chapter_order": [],
            "current_chapter_idx": 0,
            "full_rewrite": "",
            "outline": "",
            "review_report": "",
            "score": 0.0,
            "judge_verdict": "",
            "fix_list": [],
            "fact_check_report": "",
            "factual_accuracy": 0.0,
            "pdf_path": "",
            "status": "running",
            "error": "",
        }

    # 注册所有节点
    graph.add_node("parse_input", parse_input)
    graph.add_node("outline_generator", outline_generator)
    graph.add_node("writer", writer)
    graph.add_node("reviewer", reviewer)
    graph.add_node("fact_checker", fact_checker)
    graph.add_node("judge", judge)
    graph.add_node("pdf_generator", pdf_generator)

    # 入口
    graph.set_entry_point("parse_input")

    # 边
    graph.add_edge("parse_input", "outline_generator")
    graph.add_edge("outline_generator", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_edge("reviewer", "fact_checker")
    graph.add_edge("fact_checker", "judge")
    graph.add_conditional_edges(
        "judge",
        should_continue_writing,
        {
            "pdf_generator": "pdf_generator",
            "writer": "writer",
        },
    )
    graph.add_edge("pdf_generator", END)

    # 编译，加 checkpointer
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# ─── 注册到 FastAPI ───

def setup_agui_endpoint(app):
    """在 FastAPI app 上注册 AG-UI endpoint"""
    compiled_graph = build_agui_graph()

    agui_agent = LangGraphAGUIAgent(
        name="paper_rewriter",
        description="论文重写多Agent系统：提供论文标题和原文，自动完成大纲生成、章节写作、比对审查、PDF输出",
        graph=compiled_graph,
    )

    # 用桥接包装器包装，兼容 CopilotKitRemoteEndpoint
    agent = PaperRewriterAgent(agui_agent)

    sdk = CopilotKitRemoteEndpoint(agents=[agent])

    add_fastapi_endpoint(
        fastapi_app=app,
        sdk=sdk,
        prefix="/api/copilotkit",
    )

    return sdk
