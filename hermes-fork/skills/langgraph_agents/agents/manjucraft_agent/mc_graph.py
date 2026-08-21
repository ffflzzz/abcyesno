"""LangGraph definition for manjucraft_agent.

Single self-looping graph (per-episode 13-node pipeline + series loop).
The "series orchestrator" is realised as a conditional edge out of
``finalize_episode``: keep looping back to ``parse_script`` until
``current_episode`` reaches ``total_episodes``, then ``finalize_series``.

Rationale: the existing ``langgraph_runtime`` drives ONE graph via
``astream`` and snapshots THAT graph's state for HITL ``workflow.approval``
artifacts. A nested subgraph (outer ``ainvoke`` of an inner graph) would bubble
interrupts but the approval snapshot would lack per-shot artifacts. Keeping one
graph with one checkpointer satisfies all of §3.4 (character_bible, two-level
resume) without modifying the runtime.
"""

from __future__ import annotations

import httpx

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from mc_nodes.approval_gate import gate_each_scene, gate_end, gate_first_frame
from mc_nodes.batch_generate_keyframes import batch_generate_keyframes
from mc_nodes.batch_generate_video import batch_generate_video
from mc_nodes.consistency_check import consistency_check
from mc_nodes.finalize_episode import finalize_episode
from mc_nodes.finalize_series import finalize_series
from mc_nodes.fix_drift import fix_drift
from mc_nodes.generate_characters import generate_characters
from mc_nodes.generate_jianying_draft import generate_jianying_draft
from mc_nodes.generate_tts import generate_tts_node
from mc_nodes.merge_and_concat import merge_and_concat
from mc_nodes.parse_script import parse_script
from mc_nodes.plan_episodes import plan_episodes
from mc_state import AgentState


def _retry_on_transient(exc: Exception) -> bool:
    """Graph-level retry predicate: only transient infra failures.

    Connection / timeout / transport errors are worth re-running the node for.
    4xx/5xx HTTP errors are NOT retried here — 4xx is a caller bug and 5xx is
    already retried 3x at the tool level (``_post_json_with_retry`` /
    ``chat_completion``), so by the time it reaches the graph it is a genuine
    hard failure. Retrying those would just burn time on a doomed request.
    """
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


# Every node that touches the network (LLM parse, image gen, video gen, tts,
# draft) gets a graph-level retry shim for connection/timeout blips. The HITL
# gates and pure-local finalizers are intentionally excluded — interrupting /
# re-running them would corrupt the approval flow or duplicate outputs.
_NODE_RETRY = RetryPolicy(max_attempts=3, retry_on=_retry_on_transient)


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("plan_episodes", plan_episodes, retry_policy=_NODE_RETRY)
    builder.add_node("parse_script", parse_script, retry_policy=_NODE_RETRY)
    builder.add_node("generate_characters", generate_characters, retry_policy=_NODE_RETRY)
    builder.add_node("gate_first_frame", gate_first_frame)
    builder.add_node("batch_generate_keyframes", batch_generate_keyframes, retry_policy=_NODE_RETRY)
    builder.add_node("consistency_check", consistency_check)
    builder.add_node("gate_each_scene", gate_each_scene)
    builder.add_node("fix_drift", fix_drift, retry_policy=_NODE_RETRY)
    builder.add_node("batch_generate_video", batch_generate_video, retry_policy=_NODE_RETRY)
    builder.add_node("generate_tts", generate_tts_node, retry_policy=_NODE_RETRY)
    builder.add_node("merge_and_concat", merge_and_concat)
    builder.add_node("generate_jianying_draft", generate_jianying_draft, retry_policy=_NODE_RETRY)
    builder.add_node("gate_end", gate_end)
    builder.add_node("finalize_episode", finalize_episode)
    builder.add_node("finalize_series", finalize_series)

    builder.add_edge(START, "plan_episodes")
    builder.add_edge("plan_episodes", "parse_script")
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
    builder.add_edge("gate_end", "finalize_episode")

    # Series loop: after each episode, continue or finish.
    builder.add_conditional_edges(
        "finalize_episode",
        _should_continue,
        {"loop": "parse_script", "finish": "finalize_series"},
    )
    builder.add_edge("finalize_series", END)

    return builder


def _should_continue(state: AgentState) -> str:
    """Route to another episode or to series finalize."""
    return "loop" if int(state.get("current_episode", 0) or 0) < int(state.get("total_episodes", 1) or 1) else "finish"
