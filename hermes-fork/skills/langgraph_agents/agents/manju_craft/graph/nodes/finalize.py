"""Mark the workflow as complete."""

from __future__ import annotations

from graph.state import AgentState


async def finalize(state: AgentState) -> dict:
    """Finalize state and emit completion."""
    return {"status": "done"}
