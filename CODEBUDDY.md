# CODEBUDDY.md This file provides guidance to CodeBuddy when working with code in this repository.

Abcyesno is an Electron + React desktop wrapper around a **forked Hermes agent harness** (`hermes-fork/`, a large Python codebase). It reuses Hermes's agent loop, tools, skills, memory and gateway, strips IM/update/telemetry/cron modules, and exposes a CopilotKit-powered frontend. LangGraph agents (e.g. `manju_craft` video generation) are plugged in as Hermes skills. The product is a portable,免安装 Windows app.

## Common Commands

### Install dependencies
`npm install` — installs Node deps and runs `postinstall` (`electron-builder install-app-deps`). Requires Node ≥20 and a Python venv at `hermes-fork/.venv` (see `docs/SETUP.md`).

### Development mode
`npm run dev` — concurrently starts the Vite dev server (http://localhost:5173) and Electron (`electron .`). Live-edits `src/` and `electron/`.

### Build the frontend only
`npm run build` — `vite build` → `dist/`. Fast check that the React app compiles.

### Build the Electron app
`npm run electron:build` — `vite build` + `electron-builder --win`, producing `release/Abcyesno 1.3.0.exe` (portable single-file, ~325MB) and `release/win-unpacked/`.
`npx electron-builder --win --dir` — build only `win-unpacked/` (skips the slow 325MB zip); preferred for local iteration.

### Network note for builds (important)
electron-builder downloads the Electron 33 binary from GitHub. Offline it hangs/fails. Set `http_proxy`/`https_proxy` (e.g. `http://127.0.0.1:7897`) or rely on the cached `ELECTRON_CACHE` (`%LOCALAPPDATA%/electron/Cache`). DEV_LOG shows a 34-minute "hang" caused precisely by this.

### Lint / tests
No lint or test scripts exist in the root `package.json`. Syntax-check the main process manually: `node -c electron/main.js` and `node -c electron/preload.js`. The React frontend has no ESLint config; rely on `npm run build` to catch errors. Hermes fork has pytest under `hermes-fork/tests/` (run from inside `.venv` if needed).

### Inspect the Hermes backend standalone
The Python backend is normally spawned by `electron/backend/hermes-runner.js`. To run it for debugging:
```
cd hermes-fork
.venv\Scripts\python.exe -m hermes_cli.main serve --port 9120 --host 127.0.0.1 --skip-build
```
Set `HERMES_HOME` to a writable dir and `MANJU_CRAFT_MOCK=1` to avoid burning Agnes credits. Check readiness with `curl http://127.0.0.1:9120/api/status`.

### Run a single LangGraph agent (backend)
```
.venv\Scripts\python.exe -c "from tools.langgraph_agent_tool import run_agent; print(run_agent('manju_craft','一只小猫在草地上玩耍'))"
```
Set `MANJU_CRAFT_MOCK=1` for a smoke run that doesn't call Agnes image/video APIs.

### Compile-check backend Python
`python -m py_compile hermes-fork/skills/langgraph_agents/langgraph_runtime.py` (and any `agent.py`).

### Gateway smoke test
`node scripts/test-gateway.js` exercises the AG-UI bridge endpoints locally.

## Architecture

The app is a **six-layer pipeline**; a chat message travels frontend → bridge → gateway client → Hermes → back as streamed AG-UI events.

```
React + CopilotKit frontend (src/)
   │ HTTP/SSE POST /api/ag-ui/run
   ▼
AG-UI Runtime Bridge (electron/backend/agui-server.js, Express)
   │ JSON-RPC over WebSocket (ws://127.0.0.1:9120/api/ws)
   ▼
GatewayClient (electron/backend/gateway-client.js)
   │
   ▼
Hermes Fork backend (hermes-fork/, Python "hermes serve")
   │
   ▼
LangGraph agent skills (hermes-fork/skills/langgraph_agents/)
```

### Layer 1 — React frontend (`src/`)
`src/main.jsx` renders a `Bootstrap` that polls `window.hermes.getAguiPort()`; until the backend is ready it shows a loading splash. Once `aguiPort` is known it mounts `App.jsx`, which wraps everything in `<CopilotKit runtimeUrl="http://127.0.0.1:<port>/api/ag-ui/run" agent={...} threadId={...}>`. All chat goes through CopilotKit hooks (`useCopilotChatInternal`). UI pieces live in `src/components/` (Sidebar, ChatLayout, Composer, MessageThread, ApprovalDialog, SkillPanel, MarketPanel, SettingsPanel, the contract renderers ContractForm/ArtifactCard/WorkflowTimeline).

The frontend talks to **two** surfaces: (a) CopilotKit/Chat over the AG-UI SSE endpoint, and (b) a `window.hermes` object (defined in `electron/preload.js`) for non-chat operations — assistant/session CRUD, approvals, file upload, API-key management. `App.jsx` is the orchestration hub: it loads assistants/sessions/skills, wires IPC events (`approval-request`, `gateway-status`, `agui-ready`) to React state, and persists message history via `hermes.updateSession`.

### Layer 2 — Electron Main (`electron/main.js`)
Owns the lifecycle. On `app.whenReady()` it creates the window **immediately** (so the user sees the splash) then `startBackend()` in the background. It instantiates `HermesRunner` (spawns the Python process), starts the AG-UI `aguiServer` on port 9121, connects the `GatewayClient` to `ws://127.0.0.1:9120/api/ws`, and forwards backend readiness to the renderer via the `agui-ready` / `gateway-status` IPC events. All `window.hermes` methods are implemented here as `ipcMain.handle` handlers; assistant/session data persists through `Storage` (`electron/backend/storage.js`, JSON files under `HERMES_HOME`). `HERMES_HOME` is pinned to `app.getPath('userData')` → `%USERPROFILE%/.hermes_portable_data` so config, API keys and sessions survive even when the single-file portable is extracted to a temp dir.

### Layer 3 — AG-UI Runtime Bridge (`electron/backend/agui-server.js`)
This is the heart of the integration and the file you will most often edit. It is **not** a generic CopilotKit runtime — it is a hand-written Express adapter that speaks both the AG-UI SSE protocol (to the frontend) and Hermes's gateway JSON-RPC (via the GatewayClient). Key responsibilities:
- `GET /api/ag-ui/run/info` — lists agents (from `Storage.listAssistants`, falling back to a `default` 通用助手).
- `GET /api/ag-ui/contract/manifests` — aggregates `manifest.json` from each agent package under `hermes-fork/skills/langgraph_agents/agents/*`.
- `POST /api/ag-ui/run` — dispatches `agent/connect`, `agent/run`, `agent/stop`. For a run it ensures a Hermes session (`session.create`), waits for `session.info`, then `prompt.submit`, and streams the reply.
- `createTurnTranslator` — converts Hermes gateway events into AG-UI events (`TEXT_MESSAGE_START/CONTENT/END`, `TOOL_CALL_*`, `RUN_ERROR`, `CUSTOM`). **Crucially it deduplicates text**: Hermes can emit the same sentence via `message.delta`, `thinking.delta`, `status.update` and `message.complete`, so the translator keeps an `emittedText` buffer and skips deltas already present (longest-suffix overlap). Removing or weakening this causes doubled replies.
- `handleAgentRun` prompt rewriting — when the selected assistant is `manju-craft` (or the default assistant receives a video-related prompt detected by `looksLikeVideoTask()`), it injects an explicit instruction forcing the model to call the `langgraph_agent` tool with `agent_name: manju_craft`. Structured invocations from a ContractForm bypass this rewrite.

### Layer 4 — GatewayClient (`electron/backend/gateway-client.js`)
A thin WebSocket JSON-RPC 2.0 client to Hermes `/api/ws` (token query-param auth). Exposes `request(method, params)` (promise, with timeout) and `notify`, and re-emits each `event` message as `(type, params)`. Implements auto-reconnect with exponential backoff and UTF-8-safe parsing. This is the only channel that talks the raw Hermes protocol; everything else goes through it.

### Layer 5 — Hermes Fork backend (`hermes-fork/`)
A vendored, stripped copy of the Hermes agent framework (Python). `electron/backend/hermes-runner.js` launches `hermes serve --port 9120 --skip-build` via the venv Python (`-m hermes_cli.main`) or `hermes.exe`, with env vars: `HERMES_HOME`, `HERMES_TUI_TOOLSETS=hermes-cli`, `HERMES_TUI_SKILLS=langgraph-agents`, `MANJU_CRAFT_MOCK=1`, `HERMES_TUI_MAX_TURNS=15`, and `PYTHONPATH=hermes-fork` so it runs from any directory. It mirrors the builtin `skills/langgraph_agents` into `HERMES_HOME/skills` so preloading finds it. You typically do **not** edit Hermes core; treat it as a library.

### Layer 6 — LangGraph agent skills (`hermes-fork/skills/langgraph_agents/`)
The integration point for custom workflows. `langgraph_runtime.py` auto-discovers agent packages under `agents/`, each containing `agent.py` (exposing a compiled `graph`/`workflow` or `build_graph()`) and optionally `manifest.json` + `build_initial_state()` / `build_initial_state_obj()`. `tools/langgraph_agent_tool.py` registers the Hermes tool `langgraph_agent` (in toolset `hermes-cli`); the tool calls `run_agent(agent_name, input)` and streams progress via `on_event`. Bundled agents: `hello_agent`, `manju_craft` (video), `image_gen`.

### The "contract" layer (data-driven workflow UI)
The frontend renders **any** LangGraph workflow with zero component branching. Each agent ships a `manifest.json` (id, input_schema JSON-Schema with `x-ui` control hints, output_schema, approval_gates). The backend `GET /api/ag-ui/contract/manifests` is the source of truth; `src/contract/registry.js` fetches it and falls back to `src/contract/manifests.js`. `ContractForm.jsx` renders the form, `ArtifactCard.jsx` renders outputs, `WorkflowTimeline.jsx` + `src/contract/eventBus.js` + `src/hooks/useContractEvents.js` render progress. Hermes events `workflow.progress/.artifact/.approval/.error/.done` are translated by the bridge into AG-UI `CUSTOM` events. **To add a workflow: add a backend agent + its `manifest.json` (and optionally a bundled fallback in `manifests.js`). Do not add `if (workflowId===...)` branches anywhere.**

### Key invariants to preserve
- `HERMES_HOME` must always point to `userData`, never the exe dir.
- The TurnTranslator dedup logic must stay — regressions produce doubled text.
- Tool START/END matching relies on a stable `tool-${toolName}` id when Hermes sends no `tool_call_id`.
- API keys are never hardcoded; they come from `AGNES_API_KEY` env / `config.yaml` / `delegation`.
- Builds need a working GitHub/proxy path for the Electron binary.

### Logs (for debugging)
`%USERPROFILE%/.hermes_portable_data/logs/electron.log` and `.../hermes.log`. The backend also writes to the same dir.
