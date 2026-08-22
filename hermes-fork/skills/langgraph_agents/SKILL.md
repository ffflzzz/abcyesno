---
name: langgraph-agents
description: Delegate tasks to LangGraph agents through the Hermes harness. Agents are discovered from manifest.json files beside each agent package — exposure, input schema, and UI wiring are pure data.
version: 0.2.0
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

Use this skill when you want to hand off a task to a LangGraph agent (a
multi-node state machine / workflow) instead of solving it inline with Hermes
tools. Delegation is **manifest-driven**: the bridge, the frontend, and the
`langgraph_agent` tool all discover agents from data, never from per-agent
hardcoded branches.

## Tool

- **`langgraph_agent`**
  - `agent_name` (string, required): The agent package id (its manifest `id`).
    Only non-hidden agents are invocable.
  - `input` (string | object, required): Free text OR a structured object
    matching the agent's `input_schema` (contract L2).
  - `thread_id` (string, optional): A thread id; a stable one is derived when
    omitted.

This is an **asynchronous** tool: it returns a `started` envelope immediately
and the workflow runs on a background thread. Progress, artifacts, approval
gates, and completion arrive as `workflow.*` events — never call the tool again
to poll.

```json
{
  "agent_name": "manjucraft_agent",
  "input": { "mode": "single", "script": "一只小猫在草地上玩耍", "style": "二次元" }
}
```

## Available agents

- `manjucraft_agent` — the single production workflow (短剧制片工作台). Turns a
  script into a vertical short-drama/manju video: script parsing → characters →
  first-frame/storyboard approval gates → video/TTS → merge → Jianying draft
  export. Supports `single` and `series` modes (series locks a character bible
  across episodes). Reads the Agnes API key from `AGNES_API_KEY` or the active
  Hermes config.

- `paper_rewriter_agent` — 论文重写工作台 (new). A ReAct agent that rewrites a
  paper for a target audience: multi-source paper search (arXiv / Semantic
  Scholar / CrossRef / PubMed), chapter-by-chapter rewriting with self-review,
  outline gate, and a PDF export. Exposes three HITL approval gates
  (`save_outline`, `write_chapter`, `download_paper`). Launcher entry "论文重写"
  opens its form workbench in a new tab. Reads the Agnes API key from the active
  Hermes config (no hardcoded key). Runtime LLM via `langchain-openai`
  (ChatOpenAI + bind_tools); PDF export via `fpdf2`. ReAct loop needs a high
  `RECURSION_LIMIT` (400) — injected by the runtime, not hardcoded here.

For a credit-free smoke test, set `MANJUCRAFT_AGENT_MOCK=1` before invoking the
agent — the LLM / image / video / TTS / ffmpeg service layers fall back to local
stubs, so the full graph (incl. HITL gates and the series loop) runs offline.
See `scripts/smoke_manjucraft.py` for a ready-made series smoke test.

## Agent manifest contract (L1)

Each agent package lives at `agents/<id>/` and ships two files:

1. `agent.py` — exposes a compiled `graph` / `workflow`, or a `build_graph()`
   factory. May also define `build_initial_state(text)` /
   `build_initial_state_obj(obj)`, `WORKFLOW_STAGES`, and `summarize_state`.
2. `manifest.json` — pure data describing the agent to the bridge + frontend:

```jsonc
{
  "id": "my_agent",              // tool agent_name + delegation id
  "name": "我的工作流",           // display name
  "hidden": false,               // true = test/demo/legacy, never exposed
  "entry": "agents/my_agent/agent.py",
  "runtime": "inprocess",
  "skill_id": "langgraph-agents", // optional Hermes skill mapping
  "input_schema": { "type": "object", "properties": { /* L2 */ } },
  "output_schema": { "summary": "markdown", "artifacts": [ /* L3 */ ] },
  "capabilities": ["..."],
  "approval_gates": [ { "gate_id": "...", "label": "...", "allowSteer": true } ],
  "progress_events": ["workflow.progress", "workflow.artifact", "workflow.done"],
  "ui": { "type": "workbench", "component": "StudioWorkbench", "title": "..." },
  "launcher": { "title": "漫剧go", "icon": "film", "color": "..." }
}
```

`ui` and `launcher` are optional frontend metadata. If `launcher` is present the
agent appears on the app homepage; if `ui.component` is present it opens in a
dedicated workbench, otherwise the generic ContractForm renders from
`input_schema`.

## Adding an agent (data-driven, no frontend edits)

1. Create `agents/<id>/agent.py` exposing a graph.
2. Create `agents/<id>/manifest.json` (above), with `hidden: false` (or omit it)
   plus optional `ui` / `launcher` metadata.
3. Rebuild. The build-time codegen (`scripts/gen-contract.mjs`, wired into the
   Vite plugin) scans the manifests and injects the bundled contract + whitelist
   + launcher entries. The backend (`discover_manifests` / `discover_agents`)
   reads the same files at runtime and honors `hidden`.
