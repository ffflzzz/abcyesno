#!/usr/bin/env python3
"""Hello Agent -- a minimal one-node LangGraph agent.

The agent greets the user and echoes their input, backed by a real LLM call
to the Agnes AI OpenAI-compatible endpoint.
"""

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState

from langgraph_agents.langgraph_runtime import AgnesLLM

_llm = AgnesLLM(system_prompt="You are a friendly assistant. Greet the user and echo their input concisely.")


def greet_node(state: MessagesState) -> dict:
    """Call the LLM with the conversation messages and return its reply."""
    response = _llm.invoke(state["messages"])
    # Surface the greeting text in state so the runtime can emit a
    # workflow.artifact event (contract L3) for the generic workbenches.
    greeting = response.content if hasattr(response, "content") else str(response)
    return {"messages": [response], "greeting": greeting}


# Contract layer (L3/L5): ordered stages drive progress % and the per-node
# status in the generic BlueprintWorkbench. Node name must match the graph
# node so the runtime's emit_progress maps correctly.
WORKFLOW_STAGES = [
    ("greet", "问候解析"),
]

workflow = StateGraph(MessagesState)
workflow.add_node("greet", greet_node)
workflow.set_entry_point("greet")
workflow.set_finish_point("greet")

# Expose the compiled graph for the runtime loader.
graph = workflow.compile()
