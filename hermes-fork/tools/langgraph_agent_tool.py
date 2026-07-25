#!/usr/bin/env python3
"""LangGraph Agent Tool -- delegate tasks to LangGraph agents.

Exposes a single ``langgraph_agent`` tool that routes calls into the
``langgraph_agents.langgraph_runtime`` module. Agents live under
``skills/langgraph_agents/agents/<agent_name>/agent.py``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

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

    The agui-server writes ``workflow_hitl/.wf_active.json`` before submitting
    the prompt, so the tool can map its HITL events back to the correct SSE
    subscriber on the Node side.
    """
    home = os.environ.get("HERMES_HOME")
    if not home:
        return None
    coord = Path(home) / "workflow_hitl" / ".wf_active.json"
    try:
        if coord.exists():
            data = json.loads(coord.read_text(encoding="utf-8"))
            return data.get("runId")
    except Exception:
        return None
    return None


def langgraph_agent(args: Dict[str, Any], **kwargs) -> str:
    """Invoke a LangGraph agent by name.

    Args:
        args: Tool arguments. Expected keys:
            - agent_name (str, required)
            - input (str, required)
            - thread_id (str, optional)

    Returns:
        JSON string with the agent output or an error payload.
    """
    if not _LANGGRAPH_AVAILABLE:
        return tool_error(_LANGGRAPH_ERROR)

    agent_name = args.get("agent_name")
    input_text = args.get("input")
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

    # Best-effort progress/artifact forwarding (contract L5). The Hermes tool
    # framework may pass an event emitter; if so, surface it to the runtime.
    emit = kwargs.get("emit") or kwargs.get("on_event")

    # When the frontend is driving a HITL workflow (manju_craft approval gates),
    # the agui-server publishes a workflowRunId in a coordination file. Use it to
    # build an HTTP emitter so the runtime's workflow.* events reach the
    # frontend SSE stream instead of being swallowed by the non-HITL path.
    if not emit:
        explicit_run_id = args.get("workflow_run_id")
        active_run_id = _read_active_workflow_run_id()
        wf_run_id = explicit_run_id or active_run_id
        if wf_run_id:
            emit = _make_http_emitter(wf_run_id)

    result = run_agent(
        agent_name.strip(),
        input_text=input_text,
        thread_id=thread_id,
        input_obj=input_obj,
        on_event=emit,
    )
    return tool_result(result)


def check_langgraph_requirements() -> bool:
    """The tool is available when the LangGraph runtime loaded cleanly."""
    return _LANGGRAPH_AVAILABLE


LANGGRAPH_AGENT_SCHEMA = {
    "name": "langgraph_agent",
    "description": (
        "Delegate a task to a LangGraph agent. Use this when the user's request "
        "maps to a small workflow that is implemented as a LangGraph agent.\n\n"
        "Available agents: " + _available_agents_text() + "\n\n"
        "Example:\n"
        '{"agent_name": "hello_agent", "input": "world", "thread_id": "demo-1"}'
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
                "description": "Optional thread id for the agent invocation.",
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
