"""LangGraph definition for the ManjuCraft workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.approval_gate import (
    gate_each_scene,
    gate_end,
    gate_first_frame,
)
from graph.nodes.batch_generate_keyframes import batch_generate_keyframes
from graph.nodes.batch_generate_video import batch_generate_video
from graph.nodes.consistency_check import consistency_check
from graph.nodes.finalize import finalize
from graph.nodes.fix_drift import fix_drift
from graph.nodes.generate_characters import generate_characters
from graph.nodes.generate_jianying_draft import generate_jianying_draft
from graph.nodes.generate_tts import generate_tts_node
from graph.nodes.merge_and_concat import merge_and_concat
from graph.nodes.parse_script import parse_script
from graph.state import AgentState


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("parse_script", parse_script)
    builder.add_node("generate_characters", generate_characters)
    builder.add_node("gate_first_frame", gate_first_frame)
    builder.add_node("batch_generate_keyframes", batch_generate_keyframes)
    builder.add_node("consistency_check", consistency_check)
    builder.add_node("gate_each_scene", gate_each_scene)
    builder.add_node("fix_drift", fix_drift)
    builder.add_node("batch_generate_video", batch_generate_video)
    builder.add_node("generate_tts", generate_tts_node)
    builder.add_node("merge_and_concat", merge_and_concat)
    builder.add_node("generate_jianying_draft", generate_jianying_draft)
    builder.add_node("gate_end", gate_end)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "parse_script")
    builder.add_edge("parse_script", "generate_characters")
    builder.add_edge("generate_characters", "gate_first_frame")
    builder.add_edge("gate_first_frame", "batch_generate_keyframes")
    builder.add_edge("batch_generate_keyframes", "consistency_check")
    builder.add_edge("consistency_check", "gate_each_scene")
    builder.add_edge("gate_each_scene", "fix_drift")
    builder.add_edge("fix_drift", "batch_generate_video")
    builder.add_edge("batch_generate_video", "generate_tts")
    builder.add_edge("generate_tts", "merge_and_concat")
    builder.add_edge("merge_and_concat", "generate_jianying_draft")
    builder.add_edge("generate_jianying_draft", "gate_end")
    builder.add_edge("gate_end", "finalize")
    builder.add_edge("finalize", END)

    # Return the uncompiled StateGraph. The agent module compiles it once with
    # a checkpointer (required for LangGraph interrupt()/HITL). Returning the
    # builder here avoids a double-compile in agent.py.
    return builder
