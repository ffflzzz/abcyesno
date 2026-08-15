#!/usr/bin/env python3
"""LangGraph Agent Tool -- delegate tasks to LangGraph agents.

Exposes a single ``langgraph_agent`` tool that routes calls into the
``langgraph_agents.langgraph_runtime`` module. Agents live under
``skills/langgraph_agents/agents/<agent_name>/agent.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Process-level registry of background LangGraph runs. Keyed by the stable
# workflow run id so that repeated tool calls (the LLM "polling for results")
# do not spawn duplicate graphs. Each entry tracks the worker thread and a
# best-effort completion status.
_ACTIVE_RUNS: Dict[str, Dict[str, Any]] = {}

# Make ``skills/langgraph_agents`` importable as the top-level package
# ``langgraph_agents`` without restructuring the repo.
_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

# Import the runtime if LangGraph is available. If the dependencies are missing,
# register the tool with a failing check_fn so Hermes startup is unaffected.
try:
    from langgraph_agents.langgraph_runtime import list_agents, run_agent

    _LANGGRAPH_AVAILABLE = True
    _LANGGRAPH_ERROR = ""
except Exception as exc:  # pragma: no cover - defensive
    _LANGGRAPH_AVAILABLE = False
    _LANGGRAPH_ERROR = f"LangGraph runtime unavailable: {exc}"
    logger.warning(_LANGGRAPH_ERROR)

    # Dummy implementations keep the module importable.
    def list_agents() -> list:  # type: ignore[misc]
        return []

    def run_agent(*args, **kwargs) -> dict:  # type: ignore[misc]
        return {"error": _LANGGRAPH_ERROR}


def _available_agents_text() -> str:
    try:
        agents = list_agents()
        return ", ".join(agents) if agents else "(none discovered)"
    except Exception as exc:
        return f"(discovery failed: {exc})"


def _make_http_emitter(run_id: str):
    """Build an ``on_event`` callback that forwards workflow.* events to the
    agui-server over HTTP so they can be relayed to the frontend SSE stream.

    The LangGraph runtime invokes this callback during a HITL run (e.g. when a
    graph pauses at ``interrupt()``). The Python tool runs inside the Hermes
    process, so we cannot share a closure with the Node-side SSE connection; an
    HTTP POST to the local agui-server bridges the process boundary.
    """
    import urllib.request

    port = os.environ.get("AGUI_PORT") or "9121"
    url = f"http://127.0.0.1:{port}/api/ag-ui/workflow-event"

    def emit(event_type: str, payload: Any) -> None:
        try:
            body = json.dumps(
                {"type": event_type, "payload": payload, "runId": run_id}
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:  # pragma: no cover - best-effort forwarding
            logger.warning("workflow event emit failed: %s", exc)

    return emit


def _read_active_workflow_run_id() -> str | None:
    """Read the workflowRunId the agui-server stashed for the current run.

    The agui-server writes ``workflow_hitl/.wf_active_<runId>.json`` (per-run
    file) before submitting the prompt. We glob for the most recent one so
    concurrent workflows don't overwrite each other's coordination data.
    """
    home = os.environ.get("HERMES_HOME")
    if not home:
        return None
    hitl_dir = Path(home) / "workflow_hitl"
    try:
        # Per-run coord files: .wf_active_<nodeRunId>.json
        coords = sorted(
            hitl_dir.glob(".wf_active_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if coords:
            data = json.loads(coords[0].read_text(encoding="utf-8"))
            return data.get("runId")
        # Fallback: legacy single-file name (upgraded deployments may still
        # have a stale .wf_active.json on disk from an older build).
        legacy = hitl_dir / ".wf_active.json"
        if legacy.exists():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            return data.get("runId")
    except Exception:
        return None
    return None


def _stable_run_id(agent_name: str, input_text: str, input_obj: Any) -> str:
    """Derive a deterministic run id from agent + input when agui-server did
    not publish a coordination file.

    Using a hash keeps repeated LLM tool calls for the same request from
    spawning duplicate graphs, while still allowing genuinely different
    requests to run concurrently.
    """
    payload = json.dumps({"agent": agent_name, "input": input_text, "obj": input_obj}, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"wf-{agent_name}-{h}"


def _cleanup_run(request_key: str) -> None:
    """Mark a background run as finished and schedule its registry cleanup."""
    entry = _ACTIVE_RUNS.get(request_key)
    if entry:
        entry["status"] = "finished"
        entry["finished_at"] = _now()


def _now() -> float:
    import time
    return time.time()


def _request_key(agent_name: str, input_text: str, input_obj: Any) -> str:
    """Stable key for duplicate-call detection.

    We intentionally ignore ``thread_id`` because the LLM may invent a new
    thread id on each "poll for results" call. The semantic identity of the
    request is agent + normalized input.
    """
    payload = json.dumps(
        {"agent": agent_name, "input": input_text, "obj": input_obj},
        sort_keys=True,
        ensure_ascii=False,
    )
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"req-{agent_name}-{h}"


def langgraph_agent(args: Dict[str, Any], **kwargs) -> str:
    """Invoke a LangGraph agent by name — BACKGROUND JOB MODEL.

    The agent graph (incl. HITL gates + minutes of generation) is driven on a
    daemon thread by ``run_agent(background=True)``. This tool returns a
    ``started`` envelope immediately, so the Hermes gateway's per-tool execution
    timeout (420s) can never kill a long-running workflow. Progress, artifacts,
    approval gates and completion arrive asynchronously through the SSE channel
    keyed by ``wf_run_id`` — the ``wf-<runId>`` value agui-server publishes in
    the coordination file before the prompt is submitted. The frontend approval
    dialog writes its decision back to ``workflow_hitl/<wf_run_id>.json`` which
    the paused graph polls, so the HITL loop itself is unchanged.
    """
    if not _LANGGRAPH_AVAILABLE:
        return tool_error(_LANGGRAPH_ERROR)

    agent_name = args.get("agent_name")
    input_text = args.get("input", "")
    thread_id = args.get("thread_id")

    if not isinstance(agent_name, str) or not agent_name.strip():
        return tool_error("agent_name must be a non-empty string")

    # `input` may be a free-text string or a structured object (contract L2).
    # When it is a JSON-encoded object, parse it into input_obj.
    input_obj = None
    if isinstance(input_text, dict):
        input_obj = input_text
        input_text = ""
    elif isinstance(input_text, str):
        try:
            parsed = json.loads(input_text)
            if isinstance(parsed, dict):
                input_obj = parsed
                input_text = ""
        except Exception:
            pass
    else:
        return tool_error("input must be a string or object")

    # Resolve the workflow run id agui-server published for THIS run's SSE
    # stream. It is written to ``workflow_hitl/.wf_active_<ctx.runId>.json`` as
    # ``runId: 'wf-<ctx.runId>'`` right before the prompt is submitted. Using it
    # as the emitter key AND the HITL decision-file name keeps the entire event
    # / approval routing perfectly aligned with the frontend.
    wf_run_id = _read_active_workflow_run_id() or _stable_run_id(agent_name.strip(), input_text, input_obj)

    # ── Deduplication guard: repeated LLM tool calls must not spawn runs. ──
    # When the LLM sees ``status: started`` it sometimes tries to "poll for
    # results" and calls this tool again, often with a freshly generated
    # ``thread_id`` or from a new frontend run (different ``wf_run_id``).
    # We key the lock on the semantic request (agent + normalized input) so
    # duplicate calls are ignored regardless of run id churn.
    request_key = _request_key(agent_name.strip(), input_text, input_obj)
    existing = _ACTIVE_RUNS.get(request_key)
    if existing is not None:
        t = existing.get("thread")
        if t is not None and t.is_alive():
            return tool_result({
                "status": "running",
                "run_id": existing.get("wf_run_id", wf_run_id),
                "agent": agent_name.strip(),
                "message": "同一会话的工作流已在后台运行中（run_id={0}），请勿重复调用本工具。结果将通过 workflow.progress / workflow.approval / workflow.done 事件推送。".format(existing.get("wf_run_id", wf_run_id)),
            })
        # Thread died without cleanup — overwrite with a new run.
        _ACTIVE_RUNS.pop(request_key, None)

    emit = _make_http_emitter(wf_run_id)

    # Signal agui-server to keep this run's SSE open until ``workflow.done``.
    # Without this, the tool returning immediately would tear the stream down
    # and every event emitted by the background thread would be dropped.
    try:
        emit("workflow.started", {"run_id": wf_run_id, "agent": agent_name.strip()})
    except Exception:
        pass

    def _bg_wrapper() -> None:
        try:
            run_agent(
                agent_name.strip(),
                input_text=input_text,
                thread_id=wf_run_id,
                input_obj=input_obj,
                on_event=emit,
                run_id=wf_run_id,
                background=False,  # already on our own thread
                auto_approve=False,
            )
        finally:
            _cleanup_run(request_key)

    t = threading.Thread(target=_bg_wrapper, daemon=True)
    _ACTIVE_RUNS[request_key] = {
        "thread": t,
        "status": "starting",
        "started_at": _now(),
        "wf_run_id": wf_run_id,
    }
    t.start()
    return tool_result({
        "status": "started",
        "run_id": wf_run_id,
        "agent": agent_name.strip(),
        "message": "工作流已后台启动（run_id={0}）。这是异步任务，请勿再次调用 langgraph_agent 查询进度；审批门与完成结果会通过事件流推送。".format(wf_run_id),
    })


def check_langgraph_requirements() -> bool:
    """The tool is available when the LangGraph runtime loaded cleanly."""
    return _LANGGRAPH_AVAILABLE


LANGGRAPH_AGENT_SCHEMA = {
    "name": "langgraph_agent",
    "description": (
        "Delegate a task to a LangGraph agent. This is an ASYNCHRONOUS tool: "
        "call it ONCE for a given request. The workflow runs in the background, "
        "and progress/artifacts/approval gates/completion arrive as events. "
        "DO NOT call this tool again to poll for results; repeated calls for the "
        "same request will be ignored with a 'running' response.\n\n"
        "Available agents: " + _available_agents_text() + "\n\n"
        "Example (free text):\n"
        '{"agent_name": "hello_agent", "input": "world", "thread_id": "demo-1"}\n'
        "Example (structured object):\n"
        '{"agent_name": "manjucraft_agent", "input": {"mode":"single","script":"...","style":"写实"}, "thread_id": "demo-2"}'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Name of the LangGraph agent package to invoke.",
            },
            "input": {
                "type": ["string", "object"],
                "description": "User input for the agent. Either free text, or a structured object matching the agent's input_schema (contract L2).",
            },
            "thread_id": {
                "type": "string",
                "description": "Optional but recommended thread id for the agent invocation. When omitted, a stable id is derived from the agent name and input.",
            },
        },
        "required": ["agent_name", "input"],
    },
}


registry.register(
    name="langgraph_agent",
    toolset="hermes-cli",
    schema=LANGGRAPH_AGENT_SCHEMA,
    handler=langgraph_agent,
    check_fn=check_langgraph_requirements,
    emoji="🧩",
    description="Delegate a task to a LangGraph agent loaded from the langgraph-agents skill.",
)
