---
name: langgraph-agents
description: Delegate tasks to LangGraph agents through the Hermes harness.
version: 0.1.0
author: Abcyesno Project
tags:
  - langgraph
  - agents
  - delegation
  - llm
metadata:
  hermes:
    tags:
      - langgraph
      - agents
      - delegation
---

# LangGraph Agents Skill

Use this skill when you want to hand off a short, self-contained task to a
LangGraph agent instead of solving it inline with Hermes tools.

## When to use

- The task maps cleanly to a small state-machine or multi-node workflow.
- You want to keep the main Hermes conversation thin while a dedicated agent
  performs the work.
- The agent already exists under the `langgraph-agents` skill.

## Tool

- **`langgraph_agent`**
  - `agent_name` (string, required): Name of the agent package. Currently
    `hello_agent` and `manju_craft` are available.
  - `input` (string, required): The user input or task description passed to
    the agent.
  - `thread_id` (string, optional): A conversation thread id. When omitted a
    fresh id is generated.

## Example

```json
{
  "agent_name": "hello_agent",
  "input": "world",
  "thread_id": "demo-thread-1"
}
```

### ManjuCraft example

```json
{
  "agent_name": "manju_craft",
  "input": "一只小猫在草地上玩耍",
  "thread_id": "manju-demo-1"
}
```

The `manju_craft` agent expects a short script in `input` and turns it into a
video via the manju-craft LangGraph workflow (script parsing → keyframes →
consistency check → video generation → TTS → merge → Jianying draft). It reads
the Agnes API key from `AGNES_API_KEY` or from the active Hermes config.

For a credit-free smoke test, set `MANJU_CRAFT_MOCK=1` before invoking the
agent. This replaces media generation with local stubs and exercises the full
graph structure without calling image/video/TTS services.

## Available agents

- `hello_agent` — a one-node greeting/echo agent that calls the Agnes AI LLM.
- `manju_craft` — video-generation workflow adapted from the manju-craft
  project. Runs headlessly and produces `final.mp4`, `draft_content.json`, and
  `assets.zip` under `~/.manjucraft/projects/<project_name>/`.

## Adding agents

Create a new directory under `skills/langgraph_agents/agents/<agent_name>/` with
an `agent.py` that exposes either:

- a compiled graph as `graph` or `workflow`, or
- a `build_graph()` factory returning a compilable LangGraph graph.

The runtime loads the package on first use and invokes it with a
`MessagesState` containing the user's input as a `HumanMessage`.
