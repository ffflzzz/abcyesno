#!/usr/bin/env python3
"""Minimal LangGraph agent runtime for Hermes.

Discovers agent packages under ``skills/langgraph_agents/agents/`` and exposes
``list_agents()`` / ``run_agent()`` helpers used by the ``langgraph_agent`` tool.

Each agent package must contain an ``agent.py`` that exposes a compiled
LangGraph graph as ``graph`` (or ``workflow``) or a ``build_graph()`` factory.

The LLM used inside agents talks to the Agnes AI OpenAI-compatible endpoint.
Credentials are read from ``AGNES_API_KEY`` / ``AGNES_BASE_URL`` environment
variables or from the active Hermes ``config.yaml``.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import MessagesState
from langgraph.types import Command

logger = logging.getLogger(__name__)

# Bundled agent packages shipped with the skill.
_BUNDLED_AGENTS_DIR = Path(__file__).resolve().parent / "agents"

# Cache loaded agent modules by name.
_AGENT_CACHE: Dict[str, Any] = {}

DEFAULT_MODEL = "agnes-2.0-flash"
DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"


class WorkflowRejected(Exception):
    """Raised inside a LangGraph approval gate when the user rejects.

    Imported by agent gate nodes (e.g. manjucraft_agent) so the runtime can
    catch it uniformly and abort the workflow with a clean ``workflow.done``.
    """

    gate_id: Optional[str] = None

    def __init__(self, message: str = "用户拒绝工作流", gate_id: Optional[str] = None):
        super().__init__(message)
        self.gate_id = gate_id


def _user_agents_dir() -> Path:
    """Return the user-writable agent directory under HERMES_HOME/skills."""
    try:
        from hermes_constants import get_skills_dir

        return get_skills_dir() / "langgraph_agents" / "agents"
    except Exception as exc:
        logger.debug("Could not resolve user agents dir: %s", exc)
        return Path.home() / ".hermes" / "skills" / "langgraph_agents" / "agents"


def _agent_search_dirs() -> List[Path]:
    """Return agent roots in precedence order (bundled first, then user)."""
    return [_BUNDLED_AGENTS_DIR, _user_agents_dir()]


# ---------------------------------------------------------------------------
# Credential / client helpers
# ---------------------------------------------------------------------------


def _load_config_yaml() -> Dict[str, Any]:
    """Read the active Hermes config.yaml, returning an empty dict on failure."""
    try:
        from hermes_constants import get_config_path

        config_path = get_config_path()
        if not config_path.exists():
            return {}
        import yaml

        with config_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logger.debug("Could not read Hermes config for LangGraph runtime: %s", exc)
        return {}


def _get_agnes_credentials() -> tuple[str, str, str]:
    """Return (base_url, api_key, model) for the Agnes AI endpoint.

    Priority:
      1. ``AGNES_API_KEY`` / ``AGNES_BASE_URL`` / ``AGNES_MODEL`` env vars.
      2. ``providers.custom`` block in config.yaml.
      3. ``delegation.*`` block in config.yaml.
      4. Hard-coded defaults.
    """
    api_key = os.environ.get("AGNES_API_KEY", "").strip()
    base_url = os.environ.get("AGNES_BASE_URL", "").strip()
    model = os.environ.get("AGNES_MODEL", "").strip()

    cfg = _load_config_yaml()

    providers = cfg.get("providers")
    custom = {}
    if isinstance(providers, dict):
        custom = providers.get("custom", {}) or {}
    delegation = cfg.get("delegation", {}) or {}

    if not api_key:
        api_key = (custom.get("api_key") or delegation.get("api_key") or "").strip()
    if not base_url:
        base_url = (custom.get("api") or delegation.get("base_url") or "").strip()
    if not model:
        model = (
            custom.get("default_model")
            or delegation.get("model")
            or cfg.get("model", {}).get("default")
            or DEFAULT_MODEL
        ).strip()

    if not base_url:
        base_url = DEFAULT_BASE_URL
    if not model:
        model = DEFAULT_MODEL

    return base_url, api_key, model


@functools.lru_cache(maxsize=1)
def _openai_client():
    """Return a cached OpenAI client pointed at Agnes AI."""
    base_url, api_key, _ = _get_agnes_credentials()
    if not api_key:
        raise RuntimeError(
            "No Agnes API key found. Set AGNES_API_KEY or configure "
            "providers.custom.api_key in Hermes config.yaml."
        )
    import openai

    return openai.OpenAI(base_url=base_url, api_key=api_key, timeout=120.0)


class AgnesLLM:
    """Tiny OpenAI-compatible LLM wrapper for use inside LangGraph nodes.

    Accepts LangChain ``BaseMessage`` objects and returns an ``AIMessage``.
    """

    ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system"}

    def __init__(self, model: Optional[str] = None, system_prompt: Optional[str] = None):
        self.model = model or _get_agnes_credentials()[2]
        self.system_prompt = system_prompt

    def invoke(self, messages: List[Any]) -> AIMessage:
        client = _openai_client()
        openai_messages: List[Dict[str, str]] = []
        if self.system_prompt:
            openai_messages.append({"role": "system", "content": self.system_prompt})
        for msg in messages:
            role = getattr(msg, "type", "user")
            content = getattr(msg, "content", "")
            role = self.ROLE_MAP.get(role, role)
            if role not in ("user", "assistant", "system"):
                role = "user"
            openai_messages.append({"role": role, "content": str(content)})

        response = client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
        )
        content = response.choices[0].message.content or ""
        return AIMessage(content=content)


# ---------------------------------------------------------------------------
# Agent discovery
# ---------------------------------------------------------------------------


def discover_agents() -> List[str]:
    """Return sorted list of agent package names found on disk.

    Agents whose ``manifest.json`` carries ``"hidden": true`` are excluded so
    test/demo/legacy packages are never invocable via the ``langgraph_agent``
    tool, matching the manifest-driven exposure contract on the Node/frontend
    side.
    """
    seen: set = set()
    agents: List[str] = []
    for root in _agent_search_dirs():
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if d.is_dir() and (d / "agent.py").is_file() and d.name not in seen:
                mf = d / "manifest.json"
                if mf.is_file():
                    try:
                        data = json.loads(mf.read_text(encoding="utf-8"))
                        if isinstance(data, dict) and data.get("hidden"):
                            continue
                    except Exception:
                        pass
                seen.add(d.name)
                agents.append(d.name)
    return sorted(agents)


list_agents = discover_agents


def discover_manifests() -> List[Dict[str, Any]]:
    """Return manifest dicts discovered from agent packages (contract L1).

    Each agent package may ship a ``manifest.json`` describing its input/
    output schema, capabilities and approval gates. The adapter (agui-server)
    reads these to drive the generic frontend renderers.
    """
    manifests: List[Dict[str, Any]] = []
    for root in _agent_search_dirs():
        if not root.is_dir():
            continue
        for d in root.iterdir():
            mf = d / "manifest.json"
            if d.is_dir() and mf.is_file():
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("id"):
                        manifests.append(data)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("manifest parse failed %s: %s", mf, exc)
    return manifests


# ---------------------------------------------------------------------------
# Agent loading
# ---------------------------------------------------------------------------


def _agent_py_path(agent_name: str) -> Path:
    for root in _agent_search_dirs():
        candidate = root / agent_name / "agent.py"
        if candidate.is_file():
            return candidate
    # Fallback to the bundled path so the error message is helpful.
    return _BUNDLED_AGENTS_DIR / agent_name / "agent.py"


def _load_agent_module(agent_name: str) -> Any:
    """Import an agent package's ``agent.py`` once per process."""
    if agent_name in _AGENT_CACHE:
        return _AGENT_CACHE[agent_name]

    path = _agent_py_path(agent_name)
    if not path.is_file():
        raise FileNotFoundError(f"Agent '{agent_name}' not found at {path}")

    module_name = f"langgraph_agent_{agent_name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    _AGENT_CACHE[agent_name] = mod
    return mod


def _get_agent_graph(mod: Any) -> Any:
    """Resolve a compiled LangGraph graph from an agent module."""
    graph = getattr(mod, "graph", None) or getattr(mod, "workflow", None)
    if graph is None and hasattr(mod, "build_graph"):
        graph = mod.build_graph()
    if graph is None:
        raise ValueError(
            "Agent module must expose 'graph', 'workflow', or define build_graph()"
        )
    # Compile an uncompiled StateGraph on demand.
    if hasattr(graph, "compile") and not hasattr(graph, "invoke"):
        graph = graph.compile()
    return graph


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _summarize_state(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a structured summary + typed artifacts (contract L3/L5).

    Returns ``{"summary": <text>, "artifacts": [<descriptor>]}``. Agents may
    override this by defining their own ``summarize_state(result)``; otherwise
    this manju-craft-aware default is used.
    """
    status = result.get("status", "unknown")
    total = result.get("total_shots", 0)
    completed = result.get("completed_shots", 0)
    final_video = result.get("final_video_path", "")
    draft = result.get("jianying_draft_path", "")
    assets = result.get("assets_zip_path", "")
    parts = [f"status={status}", f"shots={completed}/{total}"]
    artifacts: List[Dict[str, Any]] = []
    if final_video:
        parts.append(f"final_video={final_video}")
        artifacts.append({"id": "video", "type": "video", "source": "path", "path": final_video, "label": "成片"})
    if draft:
        parts.append(f"jianying_draft={draft}")
        artifacts.append({"id": "jianying", "type": "file", "mime": "application/json", "source": "path", "path": draft, "label": "剪映草稿"})
    if assets:
        parts.append(f"assets_zip={assets}")
        artifacts.append({"id": "assets", "type": "file", "mime": "application/zip", "source": "path", "path": assets, "label": "素材包"})
    return {"summary": "; ".join(parts), "artifacts": artifacts}


def _sanitize_for_json(value: Any) -> Any:
    """Recursively convert non-JSON-serializable values to plain Python types.

    Sensitive keys such as API keys are replaced with a redaction marker.
    LangGraph Interrupt / GraphInterrupt objects are converted to their ``.value``
    dict (or ``str()`` as fallback) so they survive ``json.dumps`` in
    ``tool_result()``.
    """
    import numpy as np

    # Handle LangGraph interrupt objects that leak into return values when
    # a graph with interrupt() nodes is invoked outside the HITL streaming path.
    if hasattr(value, "__class__"):
        cls_name = type(value).__name__
        if cls_name == "Interrupt" and hasattr(value, "value"):
            return _sanitize_for_json(value.value)
        if cls_name in ("GraphInterrupt",):
            # GraphInterrupt wraps a list of Interrupt instances.
            if hasattr(value, "interrupts") and isinstance(value.interrupts, list):
                return [_sanitize_for_json(i) for i in value.interrupts]
            return str(value)

    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for k, v in value.items():
            if k in ("api_key", "token", "password", "secret"):
                sanitized[k] = "***"
            else:
                sanitized[k] = _sanitize_for_json(v)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return float(value) if isinstance(value, np.floating) else int(value)
    if hasattr(value, "item"):  # numpy scalar generic
        return value.item()
    return value


def _invoke_graph(graph: Any, input_state: Any, config: Dict[str, Any]) -> Any:
    """Invoke a compiled graph, falling back to async when nodes are async.

    LangGraph raises a TypeError when ``invoke()`` is called on a graph that
    contains asynchronous nodes. In that case we run ``ainvoke()`` in a fresh
    event loop.

    When the graph contains ``interrupt()`` nodes and is invoked **outside** the
    HITL streaming path (i.e. no ``on_event`` callback), LangGraph raises
    ``GraphInterrupt``.  We catch it here and return a structured dict so the
    caller (``run_agent``) can return a clean JSON-serializable result instead
    of leaking the raw Interrupt object into ``tool_result() → json.dumps``.
    """
    try:
        return graph.invoke(input_state, config=config)
    except TypeError as exc:
        msg = str(exc)
        if "No synchronous function provided" in msg or "async API" in msg:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(graph.ainvoke(input_state, config=config))
            # If we are already inside an event loop, schedule the async call.
            return asyncio.run_coroutine_threadsafe(
                graph.ainvoke(input_state, config=config), loop
            ).result()
        raise
    except Exception as exc:
        cls_name = type(exc).__name__
        # GraphInterrupt (and similar LangGraph interrupt errors) mean the
        # graph paused at an interrupt() node but nobody is streaming HITL
        # events to resume it.  Return a clean, serialisable sentinel.
        if "Interrupt" in cls_name or "interrupt" in str(exc).lower():
            return {
                "__interrupted__": True,
                "error": f"{cls_name}: {exc} (graph has interrupt() nodes – use HITL path)",
            }
        raise


def _hitl_dir() -> Path:
    """Directory for the file-based human-in-the-loop control channel.

    The runtime (paused at an ``interrupt()``) reads a decision file written by
    the frontend via agui-server. Both sides resolve the same path from
    ``HERMES_HOME`` (default ``~/.hermes_portable_data``).
    """
    base = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes_portable_data")
    return Path(base) / "workflow_hitl"


def _hitl_timeout() -> float:
    """Max seconds to wait for a human decision before aborting (env-overridable)."""
    try:
        return float(os.environ.get("ABC_HITL_TIMEOUT", "3600"))
    except ValueError:
        return 3600.0


def _collect_artifacts(state: Any) -> List[Dict[str, Any]]:
    """Extract a typed artifact list from an AgentState (contract L3)."""
    if not isinstance(state, dict):
        return []
    artifacts: List[Dict[str, Any]] = []
    # Character images. ``character_bible`` is populated (locked) only AFTER the
    # first-episode first-frame gate approves, so before that the freshly
    # generated first-frame images live in ``characters``. Use whichever exists,
    # preferring the bible once it is set (avoids duplicate artifacts after lock).
    char_source = state.get("character_bible") or []
    if not char_source:
        char_source = state.get("characters") or []
    for ch in char_source:
        if not isinstance(ch, dict):
            continue
        ref = ch.get("ref_image")
        name = ch.get("name") or "unknown"
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name))
        if ref:
            artifacts.append({"id": f"character_{safe}", "type": "image", "source": "path", "path": ref, "label": f"角色·{name}"})
        # Multi-view reference set: expose each additional angle (index >= 1) as
        # its own artifact. Index 0 is the canonical front (== ref_image) and is
        # already emitted above, so it is skipped to avoid a duplicate artifact.
        # Labels mirror mc_state.CHARACTER_VIEWS order; kept local to avoid a
        # hard import on the agent module path.
        _VIEW_LABELS = {1: "四分之三", 2: "侧面", 3: "背面"}
        for vi, vpath in enumerate(ch.get("view_images") or []):
            if vi == 0 or not vpath:
                continue
            vlabel = _VIEW_LABELS.get(vi, f"视角{vi}")
            artifacts.append({
                "id": f"character_{safe}_v{vi}",
                "type": "image",
                "source": "path",
                "path": vpath,
                "label": f"角色·{name}·{vlabel}",
            })
    episode = state.get("current_episode", 0)
    for r in state.get("shot_results") or []:
        if not isinstance(r, dict):
            continue
        idx = r.get("index")
        if r.get("keyframe_path"):
            artifacts.append({"id": f"shot_{idx}_keyframe", "type": "image", "source": "path", "path": r["keyframe_path"], "label": f"分镜图#{idx}", "episode": episode})
        if r.get("video_path"):
            artifacts.append({"id": f"shot_{idx}_video", "type": "video", "source": "path", "path": r["video_path"], "label": f"视频#{idx}", "episode": episode})
        if r.get("tts_audio_path"):
            artifacts.append({"id": f"shot_{idx}_tts", "type": "audio", "source": "path", "path": r["tts_audio_path"], "label": f"配音#{idx}", "episode": episode})
    if state.get("final_video_path"):
        artifacts.append({"id": "final_video", "type": "video", "source": "path", "path": state["final_video_path"], "label": "成片"})
    if state.get("jianying_draft_path"):
        artifacts.append({"id": "jianying_draft", "type": "file", "mime": "application/json", "source": "path", "path": state["jianying_draft_path"], "label": "剪映草稿"})
    if state.get("assets_zip_path"):
        artifacts.append({"id": "assets_zip", "type": "file", "mime": "application/zip", "source": "path", "path": state["assets_zip_path"], "label": "素材包"})
    # Generic single-image workflows (e.g. image_gen): surface the generated
    # image so the frontend receives a workflow.artifact event.
    if state.get("image_path"):
        artifacts.append({"id": "image", "type": "image", "source": "path", "path": state["image_path"], "label": "生成图"})
    if state.get("greeting") or state.get("reply"):
        text = state.get("greeting") or state.get("reply")
        artifacts.append({"id": "text", "type": "text", "source": "text", "text": text, "label": "回复"})
    return artifacts


def _extract_graph_topology(graph: Any) -> tuple:
    """Return ``(nodes, edges)`` for the live LangGraph trace panel.

    Uses ``graph.get_graph()`` which exposes the full edge list — including
    conditional (branch) edges such as the series loop-back — so the panel can
    render the real DAG instead of a guessed linear chain. Falls back to an
    empty topology if introspection fails for any reason, so the caller can
    degrade gracefully.
    """
    nodes: List[str] = []
    edges: List[Dict[str, str]] = []
    try:
        gg = graph.get_graph()
        raw_nodes = getattr(gg, "nodes", None)
        if isinstance(raw_nodes, dict):
            nodes = [k for k in raw_nodes.keys() if k not in ("__start__", "__end__")]
        raw_edges = getattr(gg, "edges", None)
        if isinstance(raw_edges, list):
            for e in raw_edges:
                src = getattr(e, "source", None)
                tgt = getattr(e, "target", None)
                if src is None and isinstance(e, (list, tuple)) and len(e) >= 1:
                    src = e[0]
                if tgt is None and isinstance(e, (list, tuple)) and len(e) >= 2:
                    tgt = e[1]
                if not src or not tgt:
                    continue
                if src in ("__start__",) or tgt in ("__end__",):
                    continue
                edges.append({"from": str(src), "to": str(tgt)})
    except Exception:
        nodes, edges = [], []
    return nodes, edges


def _run_async(coro):
    """Run a coroutine to completion regardless of the surrounding event loop.

    The ``langgraph_agent`` tool handler is synchronous, so we usually just
    call ``asyncio.run``. If we happen to be invoked from inside a running
    loop's thread, offload to a fresh loop in a worker thread instead.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


async def _wait_for_decision(hitl_dir: Path, run_id: str, timeout: float):
    """Poll the control-file channel until a decision is written (or timeout).

    The frontend (via agui-server) writes ``<run_id>.json`` with a ``decision``
    key. The file is consumed (deleted) once read so a single decision drives
    exactly one resume.
    """
    import time

    hitl_dir = Path(hitl_dir)
    hitl_dir.mkdir(parents=True, exist_ok=True)
    path = hitl_dir / f"{run_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("decision") not in (None, "pending"):
                try:
                    path.unlink()
                except OSError:
                    pass
                return data
        await asyncio.sleep(0.5)
    return None


async def _run_graph_with_hitl(
    graph: Any,
    input_state: Any,
    config: Dict[str, Any],
    on_event,
    run_id: str,
    stage_map: List[tuple],
    hitl_dir: Path,
    hitl_timeout: float,
    auto_approve: bool = False,
    agent_name: str = "",
):
    """Drive a LangGraph run with streaming progress, artifacts, and HITL.

    Uses ``astream(stream_mode="updates")`` so each yielded chunk names the
    node that ran -- this lets us map node -> ordered stage and compute a
    meaningful per-stage percentage (Gap 2) without fragile ``status`` string
    matching. When a node calls ``interrupt()``, the stream yields an
    ``__interrupt__`` chunk; we emit ``workflow.approval`` (carrying
    ``workflowRunId``) and block on the file control channel until the
    frontend writes a decision, then resume with ``Command(resume=decision)``.
    """
    import time  # noqa: F401  (kept for symmetry with callers)

    N = len(stage_map) or 1
    node_to_index = {name: i for i, (name, _label) in enumerate(stage_map)}
    emitted_stages: set = set()
    emitted_artifacts: set = set()
    last_state: Dict[str, Any] = dict(input_state) if isinstance(input_state, dict) else {}

    def emit_progress(node_name: str, status_kind: str, msg: Optional[str] = None) -> None:
        idx = node_to_index.get(node_name)
        if idx is None:
            return
        key = (node_name, status_kind)
        if key in emitted_stages:
            return
        emitted_stages.add(key)
        on_event(
            "workflow.progress",
            {
                "step_id": node_name,
                "stage": stage_map[idx][1],
                "status": status_kind,
                "completed": idx + 1,
                "total": N,
                "message": msg,
            },
        )

    def emit_artifacts(state: Any, step_id: Optional[str] = None) -> None:
        if not isinstance(state, dict):
            return
        for a in _collect_artifacts(state):
            if a["id"] in emitted_artifacts:
                continue
            emitted_artifacts.add(a["id"])
            if step_id:
                a = {**a, "step_id": step_id}
            on_event("workflow.artifact", a)

    def snapshot():
        try:
            snap = graph.get_state(config)
            return snap.values if snap else None
        except Exception:
            return None

    # ── Topology for the live trace panel (emitted once at run start) ──
    # Provide node labels from the agent's curated WORKFLOW_STAGES (which also
    # defines the canonical order) and the real edge list from the compiled
    # graph so the series loop-back is preserved. Include total episodes so the
    # panel can show "第 N/Total 集" during a series run.
    topo_nodes, topo_edges = _extract_graph_topology(graph)
    # Fallback: if graph introspection yields nothing (e.g. an exotic compiled
    # graph), derive a stable topology from the agent's own stage_map. This
    # guarantees the frontend always receives a usable DAG.
    if not topo_nodes and stage_map:
        topo_nodes = [n for n, _ in stage_map]
        for i in range(len(topo_nodes) - 1):
            topo_edges.append({"from": topo_nodes[i], "to": topo_nodes[i + 1]})
        if "finalize_episode" in topo_nodes:
            if "parse_script" in topo_nodes:
                topo_edges.append({"from": "finalize_episode", "to": "parse_script"})
            if "finalize_series" in topo_nodes:
                topo_edges.append({"from": "finalize_episode", "to": "finalize_series"})
    _topo_ids = set(topo_nodes)
    labeled_nodes = []
    _seen = set()
    for _n, _l in stage_map:
        if _n in _seen:
            continue
        _seen.add(_n)
        labeled_nodes.append({"id": _n, "label": _l})
    for _nid in topo_nodes:
        if _nid not in _seen:
            _seen.add(_nid)
            labeled_nodes.append({"id": _nid, "label": _nid})
    _total_eps = 1
    try:
        _total_eps = int((input_state or {}).get("total_episodes", 1) or 1)
    except Exception:
        _total_eps = 1
    on_event(
        "workflow.graph",
        {"agent": agent_name, "runId": run_id, "nodes": labeled_nodes, "edges": topo_edges, "totalEpisodes": _total_eps},
    )

    _prev_trace_node = None  # last node we marked "running", for done transition

    def _trace(node_name, status_kind):
        try:
            _ep = int((last_state or {}).get("current_episode", 0) or 0)
        except Exception:
            _ep = 0
        on_event("workflow.trace", {"node": node_name, "status": status_kind, "episode": _ep})

    pending = input_state
    while True:
        interrupted = None
        try:
            async for chunk in graph.astream(pending, config=config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                if "__interrupt__" in chunk:
                    interrupted = chunk["__interrupt__"]
                    break
                for node_name in chunk:
                    if node_name in ("__metadata__", "__interrupt__"):
                        continue
                    if node_name in node_to_index:
                        emit_progress(node_name, "running")
                    # Live node trace (NOT deduped — series mode re-runs nodes).
                    if node_name in node_to_index or node_name in _topo_ids:
                        if _prev_trace_node and _prev_trace_node != node_name:
                            _trace(_prev_trace_node, "done")
                        _trace(node_name, "running")
                        _prev_trace_node = node_name
                st = snapshot()
                if st is not None:
                    last_state = st
                streamed_node = next(
                    (n for n in chunk if n not in ("__metadata__", "__interrupt__")), None
                )
                emit_artifacts(last_state, streamed_node)
            else:
                # Stream completed without hitting an interrupt.
                break
        except WorkflowRejected as rej:
            on_event("workflow.error", {"message": f"工作流已被用户拒绝：{rej}"})
            on_event("workflow.done", {"status": "rejected", "gate_id": getattr(rej, "gate_id", None), "error": str(rej)})
            return last_state
        except Exception as exc:
            logger.exception("langgraph run failed")
            # Classify the failure so the UI can distinguish a network outage
            # (already retried 3x at the tool + graph level) from a real logic
            # bug. Transient infra errors that exhausted retries are reported as
            # network failures; everything else is a hard error.
            import httpx as _httpx

            is_network = isinstance(exc, (_httpx.TransportError, _httpx.TimeoutException))
            if isinstance(exc, _httpx.HTTPStatusError):
                is_network = getattr(exc.response, "status_code", 0) >= 500
            kind = "network" if is_network else "error"
            note = "（网络错误：已自动重试，仍失败）" if is_network else ""
            on_event("workflow.error", {"message": f"{str(exc)}{note}", "kind": kind})
            on_event("workflow.done", {"status": kind, "error": str(exc)})
            return last_state

        if not interrupted:
            break

        # A gate paused the graph. Surface approval + block on the decision.
        intr = interrupted[0]
        value = intr.value if hasattr(intr, "value") else intr
        gate_id = value.get("gate_id") if isinstance(value, dict) else None
        node_name = value.get("node") if isinstance(value, dict) else None
        label = value.get("label") if isinstance(value, dict) else (gate_id or "approval")
        message = value.get("message", "")
        allow_steer = bool(value.get("allowSteer", False))

        if node_name and node_name in node_to_index:
            emit_progress(node_name, "pending", message)
            _trace(node_name, "pending")

        st = snapshot()
        if st is not None:
            last_state = st
        artifacts = _collect_artifacts(last_state)
        emit_artifacts(last_state, node_name)

        on_event(
            "workflow.approval",
            {
                "workflowRunId": run_id,
                "gate_id": gate_id,
                "node": node_name,
                "label": label,
                "message": message,
                "allowSteer": allow_steer,
                "artifacts": artifacts,
            },
        )

        if auto_approve:
            decision = {"decision": "approve"}
        else:
            decision = await _wait_for_decision(hitl_dir, run_id, hitl_timeout)
        if decision is None:
            on_event("workflow.error", {"message": "审批等待超时，工作流已中止"})
            on_event("workflow.done", {"status": "timeout", "gate_id": gate_id})
            return last_state
        if isinstance(decision, dict) and decision.get("decision") == "reject":
            on_event("workflow.done", {"status": "rejected", "gate_id": gate_id})
            return last_state
        # approve / steer -> resume the paused graph.
        pending = Command(resume=decision)

    # Normal completion.
    if _prev_trace_node:
        _trace(_prev_trace_node, "done")
    emit_progress("finalize", "done")
    st = snapshot()
    if st is not None:
        last_state = st
    emit_artifacts(last_state, None)
    # If a node soft-failed (e.g. merge_and_concat recorded `status:
    # merge_failed` instead of raising so the workflow could finish and
    # generate_jianying_draft could still emit a draft), surface it as a
    # workflow.error event so the frontend run-error banner shows the actual
    # reason instead of pretending success.
    soft_err = (last_state or {}).get("merge_error") or (last_state or {}).get("error")
    soft_status = (last_state or {}).get("status")
    if soft_err and soft_status and "fail" in str(soft_status):
        on_event("workflow.error", {"message": soft_err, "node": "merge_and_concat"})
        on_event(
            "workflow.done",
            {"status": "failed", "artifacts": _collect_artifacts(last_state)},
        )
    else:
        on_event(
            "workflow.done",
            {"status": "done", "artifacts": _collect_artifacts(last_state)},
        )
    return last_state


def run_agent(
    agent_name: str, input_text: str = "", thread_id: Optional[str] = None,
    input_obj: Optional[Dict[str, Any]] = None, on_event=None,
    run_id: Optional[str] = None, background: bool = False,
    auto_approve: bool = False,
) -> Dict[str, Any]:
    """Run a discovered LangGraph agent with a single text input.

    Args:
        agent_name: Directory name under ``agents/``.
        input_text: User input or task description passed to the agent.
        thread_id: Optional LangGraph thread id (used as the configurable
            ``thread_id`` even when no checkpointer is configured).
        run_id: Optional workflow run id. Used both as the HITL decision-file
            name (``workflow_hitl/<run_id>.json``) and as the ``workflowRunId``
            carried in ``workflow.*`` events. When omitted, it defaults to
            ``thread_id`` (then a fresh uuid).
        background: When True, drive the graph on a daemon thread and return
            ``{"status": "started", "run_id": ...}`` immediately so the caller
            (e.g. the Hermes tool executor) is not blocked for the full runtime
            of a long workflow + HITL gates. Progress/approval/done events still
            flow through ``on_event`` (contract L5). Recommended for UI-driven
            invocations that must survive the gateway tool-timeout.
        auto_approve: When True, every ``interrupt()`` gate is resumed with an
            ``approve`` decision automatically (still emitting the
            ``workflow.approval`` event for visibility). Used by headless
            scripts that have no human in the loop.

    Returns:
        When ``background`` is False: dict with ``agent``, ``output``,
        ``thread_id`` and serialized ``messages``, or an ``error`` key on
        failure. When ``background`` is True: a ``started`` envelope.
    """
    try:
        available = discover_agents()
        if agent_name not in available:
            return {
                "error": f"Unknown agent '{agent_name}'. Available: {available}"
            }

        mod = _load_agent_module(agent_name)
        graph = _get_agent_graph(mod)

        effective_thread_id = thread_id or run_id or str(uuid.uuid4())
        effective_run_id = run_id or effective_thread_id
        config = {"configurable": {"thread_id": effective_thread_id}}

        # Per-agent recursion limit (contract: agent.py exposes RECURSION_LIMIT).
        # ReAct-style agents (agent↔tools↔review loops) routinely need hundreds
        # of steps; LangGraph's default cap (25) aborts them mid-rewrite with
        # GraphRecursionError. Data-driven: agents that don't define it are
        # unaffected.
        _recursion_limit = getattr(mod, "RECURSION_LIMIT", None)
        if _recursion_limit:
            try:
                config["recursion_limit"] = max(25, int(_recursion_limit))
            except (TypeError, ValueError):
                pass

        # Per-agent summary override (contract L3). Defaults to the structured
        # short-drama-aware summarizer defined above.
        summarize = getattr(mod, "summarize_state", _summarize_state)
        stage_map = getattr(mod, "WORKFLOW_STAGES", [])

        # Some agents (e.g. manjucraft_agent) define a custom state schema and
        # expose build_initial_state() / build_initial_state_obj() so the
        # runtime can map
        # the input (free text or a structured object) into the required fields.
        # Otherwise fall back to the standard MessagesState.
        if hasattr(mod, "build_initial_state_obj") and input_obj is not None:
            input_state = mod.build_initial_state_obj(input_obj)
        elif hasattr(mod, "build_initial_state"):
            input_state = mod.build_initial_state(input_text)
        else:
            input_state = MessagesState(messages=[HumanMessage(content=input_text)])

        # ── Background job mode (contract B / fire-and-forget) ──
        # The whole graph (incl. HITL gates + minutes of generation) runs on a
        # daemon thread. The tool call returns immediately with a ``started``
        # envelope; the frontend receives progress/approval/done over the SSE
        # channel routed by ``effective_run_id``. This avoids the gateway's
        # per-tool execution timeout entirely.
        if background:
            import threading

            def _bg_run() -> None:
                try:
                    _run_async(
                        _run_graph_with_hitl(
                            graph,
                            input_state,
                            config,
                            on_event,
                            effective_run_id,
                            stage_map,
                            _hitl_dir(),
                            _hitl_timeout(),
                            auto_approve=auto_approve,
                            agent_name=agent_name,
                        )
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("langgraph background run failed")
                    if callable(on_event):
                        try:
                            on_event("workflow.error", {"message": str(exc)})
                            on_event("workflow.done", {"status": "error", "error": str(exc)})
                        except Exception:
                            pass

            t = threading.Thread(target=_bg_run, daemon=True)
            t.start()
            return {
                "status": "started",
                "run_id": effective_run_id,
                "agent": agent_name,
                "message": "工作流已后台启动，进度将通过事件流实时推送",
            }

        if on_event:
            # Streaming + HITL path (contract L5): progress, artifacts, and
            # approval gates all flow through on_event; the graph may pause at
            # interrupt() and resume after the frontend writes a decision.
            result = _run_async(
                _run_graph_with_hitl(
                    graph,
                    input_state,
                    config,
                    on_event,
                    effective_run_id,
                    stage_map,
                    _hitl_dir(),
                    _hitl_timeout(),
                    auto_approve=auto_approve,
                    agent_name=agent_name,
                )
            )
        else:
            result = _invoke_graph(graph, input_state, config)

        summary = summarize(result) if callable(summarize) else {"summary": "", "artifacts": []}
        return {
            "agent": agent_name,
            "output": summary.get("summary", "") if isinstance(summary, dict) else summary,
            "artifacts": summary.get("artifacts", []) if isinstance(summary, dict) else [],
            "thread_id": effective_thread_id,
            "state": _sanitize_for_json(result),
            "messages": [],
        }
    except Exception as exc:
        logger.exception("run_agent failed for %s", agent_name)
        return {"error": f"{type(exc).__name__}: {exc}"}
