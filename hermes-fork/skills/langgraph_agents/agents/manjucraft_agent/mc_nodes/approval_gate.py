"""Human-in-the-loop approval gates for manjucraft_agent.

Series-aware gating (debt avoided: no N×3 popup bombardment):
  * Episode 0 (first): full first_frame / each_scene / end gates. The
    first_frame approve LOCKS ``character_bible`` so later episodes stay
    consistent.
  * Episode > 0 (later): first_frame becomes a lightweight ``episode_ready``
    gate; each_scene / end are skipped (consistency already guaranteed by the
    bible). Net gates per series = 3 + (N-1).

Each gate calls ``langgraph.types.interrupt()`` which pauses the (single,
self-looping) graph until the Abcyesno frontend resumes it with a decision.
On resume, LangGraph re-runs the gate node, so ``interrupt()`` returns the
resume value and ``_apply_decision`` runs. A ``reject`` raises
``WorkflowRejected`` so the runtime aborts cleanly.
"""

from __future__ import annotations

from langgraph.types import interrupt

# Imported by the runtime to catch rejections uniformly across agents.
from langgraph_agents.langgraph_runtime import WorkflowRejected
from mc_state import AgentState


def _apply_decision(decision) -> dict:
    """Process the resume value from ``interrupt()``.

    Returns a state update (empty unless steering notes were provided).
    Raises ``WorkflowRejected`` when the user rejects, aborting the workflow.
    """
    if isinstance(decision, dict):
        kind = decision.get("decision")
        if kind == "reject":
            raise WorkflowRejected(decision.get("reason") or "用户拒绝工作流")
        steer = decision.get("steerText") or decision.get("steer") or ""
        if steer and kind in ("steer", "approve", None):
            return {"steer_notes": steer}
    return {}


def _current_episode(state: AgentState) -> int:
    return int(state.get("current_episode", 0) or 0)


def _keyframe_artifacts(state: AgentState) -> list[dict]:
    """Collect first-frame / keyframe image descriptors from the latest
    batch_generate_keyframes result so the chat-side ApprovalBubble can
    render a thumbnail without an extra round-trip. Each entry matches the
    shape ApprovalBubble expects (``type === "image"`` and a resolvable
    ``path``); ApprovalBubble's useResolvedArtifacts hook reads the path
    via the main-process readLocalImage IPC and turns it into a data: URL
    the sandboxed renderer can load. Keep the payload minimal — no
    abcyesno-local:// url, since ApprovalBubble's localPathOf only
    recognises http:/data: as remote and would otherwise try (and fail) to
    IPC-resolve the URL."""
    out: list[dict] = []
    for sr in state.get("shot_results", []) or []:
        p = sr.get("keyframe_path")
        if not p:
            continue
        idx = sr.get("index")
        label = f"分镜 {idx + 1} 首帧" if isinstance(idx, int) else "首帧"
        out.append({
            "type": "image",
            "path": p,
            "label": label,
        })
        # first-frame gate is one approval per episode; cap the inline
        # thumbnail grid so the chat bubble stays compact.
        if len(out) >= 4:
            break
    return out


def gate_first_frame(state: AgentState) -> dict:
    """First-frame approval (episode 0) OR lightweight per-episode ready gate.

    Episode 0 -> full first-frame gate; on approve, lock ``character_bible``
    from the freshly-generated characters so later episodes reuse them.
    Episode > 0 -> lightweight ``episode_ready`` gate (no bible lock).
    """
    ep = _current_episode(state)
    if ep == 0:
        # Pull the first keyframe path out of state so the chat-side
        # ApprovalBubble has something to render as a thumbnail — the gate
        # interrupt payload flows through workflow.approval → Abcyesno
        # front-end, where ApprovalBubble reads approval.artifacts (with
        # toolMessages/fallbackImages fallbacks that don't apply here since
        # first_frame images live in shot_results, not in adjacent tool
        # messages).
        first_frame_artifacts = _keyframe_artifacts(state)
        decision = interrupt({
            "gate_id": "first_frame",
            "node": "gate_first_frame",
            "label": "首帧确认",
            "message": "角色首帧与分镜风格已生成，确认后继续生成分镜图（此确认将锁定跨集角色圣经）。",
            "allowSteer": True,
            "artifacts": first_frame_artifacts,
        })
        update = _apply_decision(decision)
        # Lock the character bible from this episode's generated characters.
        update["character_bible"] = list(state.get("characters", []))
        return update

    # Later episodes: lightweight confirmation of this episode's plan.
    decision = interrupt({
        "gate_id": "episode_ready",
        "node": "gate_first_frame",
        "label": "本集确认",
        "message": f"第 {ep + 1} 集脚本与分镜已就绪（角色沿用首集圣经），确认后开始生成。",
        "allowSteer": True,
    })
    return _apply_decision(decision)


def gate_each_scene(state: AgentState) -> dict:
    """Per-scene approval. Only on episode 0; skipped (pass-through) otherwise."""
    if _current_episode(state) != 0:
        return {}
    decision = interrupt({
        "gate_id": "each_scene",
        "node": "gate_each_scene",
        "label": "分镜确认",
        "message": "全部分镜图已生成并通过一致性检查，确认后进入视频生成。",
        "allowSteer": True,
    })
    return _apply_decision(decision)


def gate_end(state: AgentState) -> dict:
    """End approval. Only on episode 0; skipped (pass-through) otherwise."""
    if _current_episode(state) != 0:
        return {}
    decision = interrupt({
        "gate_id": "end",
        "node": "gate_end",
        "label": "成片确认",
        "message": "成片、剪映草稿与素材包已就绪，确认后导出。",
        "allowSteer": False,
    })
    return _apply_decision(decision)
