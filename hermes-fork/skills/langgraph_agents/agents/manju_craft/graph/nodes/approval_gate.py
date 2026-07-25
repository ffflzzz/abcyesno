"""Human-in-the-loop approval gates for the manju-craft workflow.

Each gate calls ``langgraph.types.interrupt()`` which pauses graph execution
until the Abcyesno frontend (the HITL brake / ApprovalDialog) resumes it with a
decision. The interrupt *value* describes the gate so the generic frontend
renderer and the runtime's file-based control channel can route the decision
back to the correct paused graph.

Re-execution note: when the graph is resumed, LangGraph **re-runs** the gate
node, so ``interrupt()`` returns the resume value and ``_apply_decision`` runs.
A ``reject`` raises ``WorkflowRejected`` so the runtime can abort cleanly.
"""

from __future__ import annotations

from langgraph.types import interrupt

# Imported by the runtime to catch rejections uniformly across agents.
from langgraph_agents.langgraph_runtime import WorkflowRejected


def _apply_decision(decision) -> dict:
    """Process the resume value returned by ``interrupt()``.

    Returns a state update (empty unless steering notes were provided).
    Raises ``WorkflowRejected`` when the user rejects, aborting the workflow.
    """
    if isinstance(decision, dict):
        kind = decision.get("decision")
        if kind == "reject":
            raise WorkflowRejected(decision.get("reason") or "用户拒绝工作流")
        steer = decision.get("steerText") or decision.get("steer") or ""
        # Steering notes apply for an explicit "steer" decision, and defensively
        # also when a plain "approve" carries non-empty notes (the frontend may
        # send approve+steerText depending on how the brake button was wired).
        if steer and kind in ("steer", "approve", None):
            return {"steer_notes": steer}
    return {}


def gate_first_frame(state) -> dict:
    """First-frame approval: pause after character/style generation."""
    decision = interrupt(
        {
            "gate_id": "first_frame",
            "node": "gate_first_frame",
            "label": "首帧确认",
            "message": "角色首帧与分镜风格已生成，确认后继续生成分镜图。",
            "allowSteer": True,
        }
    )
    return _apply_decision(decision)


def gate_each_scene(state) -> dict:
    """Per-scene approval: pause after consistency check of all keyframes."""
    decision = interrupt(
        {
            "gate_id": "each_scene",
            "node": "gate_each_scene",
            "label": "分镜确认",
            "message": "全部分镜图已生成并通过一致性检查，确认后进入视频生成。",
            "allowSteer": True,
        }
    )
    return _apply_decision(decision)


def gate_end(state) -> dict:
    """End approval: pause after the final video + Jianying draft are ready."""
    decision = interrupt(
        {
            "gate_id": "end",
            "node": "gate_end",
            "label": "成片确认",
            "message": "成片、剪映草稿与素材包已就绪，确认后导出。",
            "allowSteer": True,
        }
    )
    return _apply_decision(decision)
