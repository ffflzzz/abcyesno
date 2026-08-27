const express = require('express');
const cors = require('cors');
const net = require('net');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { EventEncoder } = require('@ag-ui/encoder');
const { v4: uuidv4 } = require('uuid');
const { log } = require('./logger');
const agnes = require('./agnes');
// edge-tts 云端语音客户端（自带 GEC 鉴权，替换坏的 edge-tts npm 包）。
const { synth: edgeTtsSynth } = require('./edgeTtsClient');

function findAvailablePort(host, startPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        findAvailablePort(host, startPort + 1).then(resolve, reject);
      } else {
        reject(err);
      }
    });
    server.once('listening', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.listen(startPort, host);
  });
}

// Minimal AG-UI Runtime Bridge: translates CopilotKit SSE requests into
// Hermes gateway JSON-RPC calls over the persistent WebSocket connection.
// Delegation is manifest-driven: no per-workflow keyword sniffing or input
// parsing lives in the bridge. The frontend entry / ContractForm / @mention
// resolves which agent to invoke (by manifest id) and passes either structured
// input (from the manifest's input_schema) or free text; the generic
// langgraph_agent tool + each agent's own build_initial_state* handle the rest.

// (resolveMentionDelegation moved inside createAgUIServer below — it closes
// over the inner discoverManifests() which is not in module scope.)

function createAgUIServer(getGatewayClient, storage, options) {

  const app = express();
  app.use(cors());
  app.use(express.json({ limit: '50mb' }));

  // Workflow HITL event subscribers (contract L4/L5): maps a workflowRunId to
  // the SSE send function of the run that spawned it. The Python langgraph
  // runtime cannot reach the Node SSE closure directly, so it POSTs events to
  // /api/ag-ui/workflow-event and we relay them to the correct SSE stream.
  const workflowSubscribers = new Map();

  // 后台 workflow 作业登记：键为 wfRunId，值为该轮 run 的 SSE 资源句柄。
  // langgraph_agent 进入 fire-and-forget 后整图转入后台线程执行，本路 SSE
  // 必须保持打开直到 workflow.done，否则后台事件会被丢弃（contract B）。
  const backgroundRuns = new Map();

  // 幂等关闭一条后台 run 占用的 SSE：清理 subscriber / 协调文件 / activeTurn，
  // 发送 RUN_FINISHED 并 end。由 workflow.done 钩子或超时兜底调用。
  function finishBackgroundRun(br) {
    if (!br || br.ended) return;
    br.ended = true;
    // clearInterval 在 null/未定义上是安全的，无需额外 try
    if (br.timer) { clearInterval(br.timer); }
    br.timer = null;
    try {
      backgroundRuns.delete(br.wfRunId);
      workflowSubscribers.delete(br.wfRunId);
      clearEventBuffer(br.wfRunId);
      const base = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
      const coordFile = path.join(base, 'workflow_hitl', `.wf_active_${br.ctx.runId}.json`);
      try { if (fs.existsSync(coordFile)) fs.unlinkSync(coordFile); } catch (_) {}
      activeTurnSenders.delete(br.ctx.runId);
      clearEventBuffer(br.ctx.runId);
      try { clearUiActiveCoord(); } catch (_) {}
      try {
        sendSSE(br.res, br.encoder, { type: 'RUN_FINISHED', threadId: br.ctx.threadId, runId: br.ctx.runId });
      } catch (_) {}
    } catch (_) { /* best-effort */ }
    try { br.res.end(); } catch (_) {}
  }

  // Agent 自渲染 UI 组件层：普通对话轮次（非 workflow）的活跃 SSE sender。
  // render_ui tool 在 Hermes 进程内通过 HTTP 桥把 ui.render 事件回传，这里按
  // runId 路由到当前对话轮的 SSE 流。与 workflowSubscribers 平行，但覆盖所有轮次。
  const activeTurnSenders = new Map();

  // ── 事件桥缓冲（重连/缓冲）──────────────────────────────────────────
  // Python 侧可在任意时刻 POST workflow.* / ui.render 事件——包括订阅者尚未
  // 注册、或 SSE 断开重连的瞬态窗口。无订阅者时不丢弃，而是按 runId 暂存，
  // 订阅者注册/重连时回放。这直接规避「workflow.started 早于订阅注册 → 后台
  // run 未置 keepOpen → SSE 被提前关闭 → 后续事件全丢」的竞态。
  const pendingEventBuffer = new Map(); // runId -> [{ event, ts }]
  const EVENT_BUFFER_MAX = 500;
  const EVENT_BUFFER_TTL_MS = 10 * 60 * 1000; // 10 分钟

  function bufferEvent(runId, event) {
    if (!runId) return;
    const now = Date.now();
    let buf = pendingEventBuffer.get(runId);
    if (!buf) {
      buf = [];
      pendingEventBuffer.set(runId, buf);
    }
    // TTL 兜底，防止 runId 永久泄漏。
    while (buf.length && now - buf[0].ts > EVENT_BUFFER_TTL_MS) buf.shift();
    // 上限兜底：丢最旧，防无界增长。
    if (buf.length >= EVENT_BUFFER_MAX) buf.shift();
    buf.push({ event, ts: now });
  }

  function drainEventBuffer(runId, send) {
    const buf = pendingEventBuffer.get(runId);
    if (!buf) return;
    pendingEventBuffer.delete(runId);
    for (const { event } of buf) {
      try {
        send(event);
      } catch (_) {
        /* best-effort */
      }
    }
  }

  function clearEventBuffer(runId) {
    pendingEventBuffer.delete(runId);
  }

  // 协调文件：每条对话轮开始时写入当前 runId，供 Python 侧 render_ui tool 发现
  // 自己属于哪个 SSE 流（与 workflow_hitl/.wf_active.json 同构）。
  function uiActiveCoordPath() {
    const base = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
    return path.join(base, 'workflow_hitl', '.ui_active.json');
  }
  function writeUiActiveCoord(runId, threadId) {
    try {
      const p = uiActiveCoordPath();
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, JSON.stringify({ runId, threadId }), 'utf-8');
    } catch (e) {
      log('agui-server', `ui coord write failed: ${e.message}`);
    }
  }
  function clearUiActiveCoord() {
    try {
      const p = uiActiveCoordPath();
      if (fs.existsSync(p)) fs.unlinkSync(p);
    } catch (e) {
      log('agui-server', `ui coord clear failed: ${e.message}`);
    }
  }

  function gatewayClient() {
    return typeof getGatewayClient === 'function' ? getGatewayClient() : getGatewayClient;
  }

  async function buildInfoResponse() {
    const list = await storage.listAssistants();
    let agents = Array.isArray(list) ? list : [];
    if (agents.length === 0) {
      agents = [{ id: 'default', name: 'ABC', description: '默认助手', skillId: 'default' }];
    }
    const agentsRecord = {};
    // Always ensure a "default" entry exists so CopilotKit can resolve the
    // fallback agent even when stored assistants use different IDs.
    const hasDefault = agents.some((a) => a.id === 'default');
    if (!hasDefault) {
      agentsRecord['default'] = {
        name: 'ABC',
        description: '默认助手',
        capabilities: [],
      };
    }
    for (const a of agents) {
      agentsRecord[a.id] = {
        name: a.name,
        description: a.description || '',
        capabilities: a.capabilities || [],
      };
    }
    return {
      status: 'ok',
      runtime: 'abcyesno',
      runtimeVersion: '1.0.0',
      agents: agentsRecord,
      defaultAgent: agents[0]?.id || 'default',
    };
  }

  app.get('/api/ag-ui/run/info', async (_req, res) => {
    try {
      res.json(await buildInfoResponse());
    } catch (err) {
      log('agui-server', `info error: ${err.message}`);
      res.json({ status: 'ok', runtime: 'abcyesno', runtimeVersion: '1.0.0', agents: {}, defaultAgent: 'default' });
    }
  });

  // --- Contract layer (L1 discovery) -------------------------------------
  // Scan agent packages for manifest.json and return the full manifest list
  // (id/name/description/input_schema/output_schema/approval_gates/...). The
  // frontend uses this as the source of truth for the generic renderers.
  function resolveAgentsDirs() {
    const opts = options || {};
    const dirs = [];
    if (opts.agentsDir) {
      dirs.push(...(Array.isArray(opts.agentsDir) ? opts.agentsDir : [opts.agentsDir]));
    }
    const cands = [
      path.resolve(__dirname, '../../hermes-fork/skills/langgraph_agents/agents'),
      process.env.ABC_LANGGRAPH_AGENTS_DIR,
    ];
    for (const c of cands) if (c) dirs.push(c);
    return dirs.filter(Boolean);
  }

  function discoverManifests() {
    const out = [];
    for (const dir of resolveAgentsDirs()) {
      let entries = [];
      try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
      } catch (e) {
        continue;
      }
      for (const ent of entries) {
        if (!ent.isDirectory()) continue;
        const mf = path.join(dir, ent.name, 'manifest.json');
        try {
          if (fs.existsSync(mf)) {
            const data = JSON.parse(fs.readFileSync(mf, 'utf8'));
            // Skip hidden agents (test/demo/legacy) so they never surface in
            // the contract endpoint nor become @mention/mentions delegation
            // targets. Production exposure is a data decision, not a code one.
            if (data && data.id && !data.hidden) out.push(data);
          }
        } catch (e) {
          log('agui-server', `manifest read failed ${mf}: ${e.message}`);
        }
      }
    }
    return out;
  }

  // --- @ mention protocol (spec §2) -----------------------------------------
  // Resolve which workflow (if any) a turn is delegated to. Sources, in order:
  //   1. explicit `mentions` array (workflow ids) from the frontend
  //   2. `@Name` / `@id` tokens embedded in the user text
  // Returns the matched manifest object, or null. The caller opens the HITL
  // subscriber keyed by this workflow so its workflow.* events relay back.
  // Defined INSIDE createAgUIServer so it closes over discoverManifests()
  // (which is function-local here, not module-scope).
  function resolveMentionDelegation(text, mentions) {
    const mfMap = new Map();
    for (const m of discoverManifests()) {
      if (m.id) mfMap.set(m.id, m);
      if (m.name) mfMap.set(m.name, m);
    }
    // 1. explicit mentions
    const targets = [];
    if (Array.isArray(mentions)) {
      for (const id of mentions) {
        const key = String(id || '').trim();
        const m = mfMap.get(key) || mfMap.get(key.replace(/_/g, ' '));
        if (m && !targets.includes(m)) targets.push(m);
      }
    }
    // 2. @token in text
    const re = /@([^\s@]+)/g;
    let mm;
    while ((mm = re.exec(text || '')) !== null) {
      const token = mm[1];
      const m = mfMap.get(token) || mfMap.get(token.replace(/_/g, ' '));
      if (m && !targets.includes(m)) targets.push(m);
    }
    return targets.length > 0 ? targets[0] : null;
  }

  app.get('/api/ag-ui/contract/manifests', (_req, res) => {
    try {
      res.json({ status: 'ok', manifests: discoverManifests() });
    } catch (e) {
      res.json({ status: 'ok', manifests: [] });
    }
  });

  function resolveRunContext(body) {
    const method = body.method;
    const isAgentMethod = method && method.startsWith('agent/');
    // For single-endpoint transport CopilotKit nests the payload under body.body.
    const payload = isAgentMethod ? (body.body || {}) : body;
    const params = isAgentMethod ? (body.params || {}) : {};
    const agentId = params.agentId || payload.agentId || body.agentId;
    const threadId = payload.threadId || body.threadId || uuidv4();
    const runId = payload.runId || body.runId || uuidv4();
    const messages = payload.messages || body.messages || [];
    const forwardedProps = payload.forwardedProps || payload.properties || body.forwardedProps || body.properties || {};
    const assistantId = forwardedProps.assistantId || agentId || 'default';
    const skillId = forwardedProps.skillId;
    const requestedModel = forwardedProps.model;
    const images = payload.images || body.images || [];
    // Per-session workspace binding (docs/SESSION_WORKSPACE_SPEC.md): the
    // frontend stores workspaceDir on the session record and echoes it back
    // in forwardedProps so every run carries its working folder.
    const workspaceDir = typeof forwardedProps.workspaceDir === 'string' && forwardedProps.workspaceDir.trim()
      ? forwardedProps.workspaceDir.trim()
      : null;
    return { method, agentId, threadId, runId, messages, forwardedProps, assistantId, skillId, requestedModel, images, workspaceDir };
  }

  // Cache recent session.status validations so we don't hit the gateway on
  // every message of an active thread (P2: reduce per-message round-trips).
  const sessionValidatedAt = new Map(); // threadId -> last validated timestamp
  const SESSION_TTL_MS = 60000;

  // Last cwd we know was applied to each thread's Hermes session. In-memory
  // only: after a bridge restart the first run re-issues session.cwd.set,
  // which is idempotent on the gateway side.
  const appliedWorkspace = new Map(); // threadId -> workspaceDir | null

  function validWorkspaceDir(dir) {
    if (!dir || typeof dir !== 'string') return null;
    try {
      if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return null;
    } catch {
      return null;
    }
    return dir;
  }

  // Push a new workspace onto an already-mapped Hermes session. Non-fatal:
  // a busy/stale gateway must not break the run — the run simply keeps the
  // previous cwd this turn.
  async function applyWorkspaceToExisting(client, threadId, hermesSessionId, workspaceDir) {
    const prev = appliedWorkspace.has(threadId) ? appliedWorkspace.get(threadId) : undefined;
    if (prev === workspaceDir) return true;
    try {
      await client.request('session.cwd.set', { session_id: hermesSessionId, cwd: workspaceDir }, 10000);
      appliedWorkspace.set(threadId, workspaceDir);
      log('agui-server', `session.cwd.set -> ${workspaceDir} (${threadId})`);
      return true;
    } catch (err) {
      log('agui-server', `session.cwd.set failed for ${threadId}: ${err.message}`);
      return false;
    }
  }

  async function ensureHermesSession(client, ctx) {
    const existing = await storage.getThreadMapping(ctx.threadId);
    if (existing) {
      const lastValidated = sessionValidatedAt.get(ctx.threadId) || 0;
      if (Date.now() - lastValidated < SESSION_TTL_MS) {
        // Trust the cached mapping within TTL — skip the gateway round-trip.
        return { id: existing, created: false };
      }
      try {
        // Validate that the mapped Hermes session is still alive (it may have
        // been lost when Hermes restarted or was evicted from memory).
        await client.request('session.status', { session_id: existing }, 5000);
        sessionValidatedAt.set(ctx.threadId, Date.now());
        // Re-apply workspace if it changed since the last run (or since the
        // bridge restarted and our applied-map is empty).
        const wsDir = validWorkspaceDir(ctx.workspaceDir);
        if (wsDir) {
          await applyWorkspaceToExisting(client, ctx.threadId, existing, wsDir);
        }
        return { id: existing, created: false };
      } catch (err) {
        log('agui-server', `existing session ${existing} not found (${err.message}), recreating`);
        await storage.setThreadMapping(ctx.threadId, null);
      }
    }

    const assistant = await storage.getAssistant(ctx.assistantId);
    const createParams = { close_on_disconnect: false };
    const effectiveModel = ctx.requestedModel || assistant?.defaultModel || assistant?.modelOverride;
    if (effectiveModel) {
      createParams.model = effectiveModel;
    }
    // Frontend assistant skill IDs are already Hermes skill IDs (from
    // listSkills); pass them through verbatim. No per-workflow mapping.
    const skillId = ctx.skillId;
    if (skillId && skillId !== 'default') {
      createParams.skill_id = skillId;
    }
    // Workspace binding: create the Hermes session directly inside the user's
    // folder so tools resolve relative paths against it from turn one.
    const wsDir = validWorkspaceDir(ctx.workspaceDir);
    if (wsDir) {
      createParams.cwd = wsDir;
    }
    log('agui-server', `session.create params: ${JSON.stringify(createParams)}`);
    let created;
    try {
      created = await client.request('session.create', createParams, 30000);
    } catch (err) {
      // A stale workspace path (folder deleted/moved) would fail the whole
      // session.create — retry once without the binding so the run survives.
      if (createParams.cwd) {
        log('agui-server', `session.create with cwd failed (${err.message}); retrying without workspace`);
        delete createParams.cwd;
        created = await client.request('session.create', createParams, 30000);
      } else {
        throw err;
      }
    }
    const hermesSessionId = created?.session_id || created?.id;
    if (!hermesSessionId) {
      throw new Error('session.create did not return a session id');
    }
    await storage.setThreadMapping(ctx.threadId, hermesSessionId);
    appliedWorkspace.set(ctx.threadId, createParams.cwd || null);
    return { id: hermesSessionId, created: true };
  }

  // ── Slash command pre-dispatch (B 方案) ──────────────────────────────
  // Mirror Hermes CLI's `_looks_like_slash_command`: a slash command is
  // "/word" where word (after the leading slash) contains no further slash —
  // this excludes filesystem paths like "/Users/foo/bar.md". We then route the
  // command through the 9120 gateway's existing `command.dispatch` JSON-RPC
  // method, which already implements /goal (+ status/pause/resume/clear),
  // /undo, /retry, /steer, /queue, /learn, /moa, /snapshot and quick/plugin/
  // skill commands. Commands the gateway doesn't recognize (e.g. /model,
  // /status, /fast — which are TUI-only and have no headless handler) fall
  // through safely to the normal LLM path instead of blocking the user.
  async function trySlashDispatch(rawText, client, ctx) {
    if (!rawText || typeof rawText !== 'string') return null;
    const m = /^\s*\/([^\s/]+)(?:\s+([\s\S]*))?$/.exec(rawText.trim());
    if (!m) return null; // not a slash command — or a path like /Users/foo
    const name = '/' + m[1];
    const arg = (m[2] || '').trim();

    // command.dispatch needs a live Hermes session to resolve goals/undo.
    let hermesSessionId;
    try {
      const r = await ensureHermesSession(client, ctx);
      hermesSessionId = r && r.id;
    } catch (e) {
      log('agui-server', `slash: ensureHermesSession failed: ${(e && e.message) || e}`);
      return null; // fall back to LLM
    }
    if (!hermesSessionId) return null;

    let dispatch;
    try {
      dispatch = await client.request(
        'command.dispatch',
        { name, arg, session_id: hermesSessionId },
        30000
      );
    } catch (e) {
      // Gateway rejected it (e.g. "_err(4018) not a quick/plugin/skill
      // command") or was unreachable — treat as an unknown command and let
      // the normal LLM path handle the text rather than blocking the user.
      log('agui-server', `slash: command.dispatch rejected ${name}: ${(e && e.message) || e}`);
      return null;
    }
    if (!dispatch || typeof dispatch !== 'object') return null;

    const type = dispatch.type;
    if (type === 'exec' || type === 'plugin') {
      return { kind: 'exec', output: dispatch.output || '' };
    }
    if (type === 'prefill') {
      // TUI semantics: prefill composer + notice. In chat we surface the
      // notice as a message and do NOT auto-submit the prefilled text.
      return { kind: 'prefill', output: dispatch.notice || dispatch.message || '' };
    }
    if (type === 'send' || type === 'skill') {
      // `/goal <text>` (and only that — pause/clear/status/resume are `exec`)
      // kicks off an autonomous cross-turn loop in the gateway, so the bridge
      // must keep the SSE open across multiple message.start/complete cycles
      // (Phase 2). Other 'send' commands (/retry, /steer, /queue, /learn,
      // /moa, alias resolution) are one-shot — no flag needed.
      const goalMode = name === '/goal' && !!(dispatch.message);
      return {
        kind: 'send',
        message: dispatch.message || '',
        notice: dispatch.notice || '',
        ...(goalMode ? { goalMode: true } : {}),
      };
    }
    if (type === 'alias') {
      // Resolve the alias target as a follow-up user turn.
      return { kind: 'send', message: dispatch.target || '', notice: '' };
    }
    // Unknown structured type — fall back to LLM with the raw text.
    return null;
  }

  function sendSSE(res, encoder, event) {
    try {
      res.write(encoder.encode(event));
    } catch (err) {
      log('agui-server', `write error: ${err.message}`);
    }
  }

  function createTurnTranslator(res, encoder, ctx, opts = {}) {
    // Phase 2: when `multiRound` is true (set by waitForHermesTurn for /goal
    // runs), every gateway message.start/complete cycle mints a fresh AG-UI
    // messageId so the frontend renders each goal iteration as its own
    // assistant bubble instead of merging them into a single rolling message.
    const { multiRound = false } = opts;
    let messageId = `msg-${ctx.runId || uuidv4()}`;
    let round = 0;
    let messageStarted = false;
    let currentRoundClosed = true; // true ⇒ next message.start can begin a new round
    let hasTextDelta = false;
    let hasRunError = false;
    let emittedText = '';
    let reasoningDeltaSeen = false; // true once we've streamed real reasoning.delta this round
    const activeToolCalls = new Set();
    const toolStartTimes = new Map(); // toolCallId -> timestamp, for duration_ms
    let currentPhase = 'idle'; // idle | thinking | tool_executing | text_generating

    function send(event) {
      sendSSE(res, encoder, event);
    }

    function setPhase(phase) {
      if (phase === currentPhase) return;
      currentPhase = phase;
      send({ type: 'CUSTOM', name: 'stream.phase', value: { phase, runId: ctx.runId } });
    }

    function nextMessageId() {
      if (!multiRound) return `msg-${ctx.runId || uuidv4()}`;
      return `msg-${ctx.runId || uuidv4()}-r${round}`;
    }

    function rotateToNewRound() {
      // End any in-flight round cleanly, then mint the next messageId and
      // reset per-round accumulators. Safe to call when nothing is open.
      if (messageStarted) {
        send({ type: 'TEXT_MESSAGE_END', messageId });
        messageStarted = false;
      }
      round += 1;
      messageId = nextMessageId();
      emittedText = '';
      reasoningDeltaSeen = false;
      hasTextDelta = false;
      activeToolCalls.clear();
      currentRoundClosed = false;
    }

    function ensureMessageStarted(role = 'assistant') {
      if (!messageStarted) {
        messageStarted = true;
        currentRoundClosed = false;
        send({ type: 'TEXT_MESSAGE_START', messageId, role });
      }
    }

    // Stream deltas are pure increments (Agnes streams digits char-wise).
    // Forward them verbatim — no dedup, no overlap stripping. Dedup here
    // would swallow legitimate single-char digits / duplicate runs and
    // garble dates & numbers. See appendStreamDelta (module-level).
    function appendDelta(delta) {
      return appendStreamDelta(delta);
    }

    function recordEmitted(delta) {
      emittedText += delta;
    }

    function emitTextDelta(delta) {
      const actual = appendDelta(delta);
      // High-frequency: only log when ABC_AGUI_DEBUG is set (avoid per-delta
      // synchronous file I/O storms during streaming).
      if (process.env.ABC_AGUI_DEBUG) {
        log('agui-server', `[translator] delta len=${delta.length} actualLen=${actual.length} emittedLen=${emittedText.length}`);
      }
      if (!actual) return;
      ensureMessageStarted();
      hasTextDelta = true;
      recordEmitted(actual);
      send({ type: 'TEXT_MESSAGE_CONTENT', messageId, delta: actual });
    }

    function emitToolStart(toolCallId, toolName, args, parentMessageId) {
      activeToolCalls.add(toolCallId);
      send({
        type: 'TOOL_CALL_START',
        toolCallId,
        toolCallName: toolName || 'tool',
        parentMessageId: parentMessageId || messageId,
      });
      if (args && Object.keys(args).length > 0) {
        send({
          type: 'TOOL_CALL_ARGS',
          toolCallId,
          delta: JSON.stringify(args),
        });
      }
    }

    function emitToolResult(toolCallId, result) {
      const content = typeof result === 'string' ? result : JSON.stringify(result || {});
      send({
        type: 'TOOL_CALL_RESULT',
        messageId,
        toolCallId,
        content,
        role: 'tool',
      });
    }

    function emitToolEnd(toolCallId, result, durationMs, failed) {
      if (result !== undefined) emitToolResult(toolCallId, result);
      const endPayload = { type: 'TOOL_CALL_END', toolCallId };
      if (durationMs !== undefined) {
        endPayload.durationMs = durationMs;
      }
      if (failed) {
        endPayload.failed = true;
      }
      send(endPayload);
    }

    function finalize(text) {
      // message.complete carries the full text. Stream deltas are pure
      // increments, so emittedText is already the correct prefix of the final
      // answer; finalizeRemainder sends only whatever is still missing (if
      // anything). This is a precise prefix/suffix check — no heuristics.
      if (text && typeof text === 'string') {
        const actual = finalizeRemainder(emittedText, text);
        if (actual) {
          ensureMessageStarted();
          hasTextDelta = true;
          recordEmitted(actual);
          send({ type: 'TEXT_MESSAGE_CONTENT', messageId, delta: actual });
        }
      }
      ensureMessageStarted();
      send({ type: 'TEXT_MESSAGE_END', messageId });
      // Phase 2: in multiRound (goal) mode, the next message.start should
      // mint a fresh messageId so each iteration is a new assistant bubble.
      // Close out the per-round accumulators and pre-rotate here.
      if (multiRound) {
        round += 1;
        messageId = nextMessageId();
        emittedText = '';
        hasTextDelta = false;
        activeToolCalls.clear();
        messageStarted = false;
        currentRoundClosed = true;
      }
    }

    function handleEvent(type, params) {
      const payload = params && params.payload ? params.payload : params;
      const sid = params && (params.session_id || params.sid);
      if (sid && sid !== ctx.hermesSessionId) return;
      // High-frequency: only log when ABC_AGUI_DEBUG is set.
      if (process.env.ABC_AGUI_DEBUG) {
        const textPreview = payload && typeof payload.text === 'string' ? payload.text.slice(0, 100) : '';
        log('agui-server', `[translator] event=${type} sid=${sid || ''} textPreview=${JSON.stringify(textPreview)}`);
      }

      switch (type) {
        case 'message.start':
          // Phase 2: in multiRound mode each gateway round should arrive with
          // a preceding message.complete (which rotates the messageId). If a
          // message.start arrives while the previous round is still open,
          // defensively close the old message before starting the new one so
          // the SSE stream stays well-formed.
          if (multiRound && messageStarted) {
            send({ type: 'TEXT_MESSAGE_END', messageId });
            round += 1;
            messageId = nextMessageId();
            emittedText = '';
            hasTextDelta = false;
            activeToolCalls.clear();
          }
          ensureMessageStarted('assistant');
          break;

        case 'message.delta':
          if (payload && typeof payload.text === 'string') {
            if (!hasTextDelta && activeToolCalls.size === 0) {
              setPhase('text_generating');
            }
            emitTextDelta(payload.text);
          }
          break;

        case 'thinking.delta':
          // Forward thinking content to frontend for the ThinkingIndicator.
          // The frontend renders it as a transient indicator, not a bubble.
          if (payload && typeof payload.text === 'string' && payload.text.trim()) {
            setPhase('thinking');
            send({ type: 'CUSTOM', name: 'thinking.delta', value: { text: payload.text } });
          }
          break;

        case 'reasoning.delta':
          reasoningDeltaSeen = true;
          // Real model reasoning tokens (from chat_completion_helpers / _fire_reasoning_delta).
          // Forwarded as its OWN CUSTOM event so the frontend can render a dedicated
          // ReasoningBlock distinct from the shallow thinking indicator.
          if (payload && typeof payload.text === 'string' && payload.text.trim()) {
            setPhase('thinking');
            send({ type: 'CUSTOM', name: 'reasoning.delta', value: { text: payload.text } });
          }
          break;

        case 'reasoning.available':
          // Fallback for models that expose thinking only as a non-streaming
          // scratchpad (_think_text in conversation_loop). If we already streamed
          // real reasoning.delta this turn, SKIP — otherwise that scratchpad (often
          // the answer body) would clobber the real reasoning and get hidden as a
          // duplicate. See #thinking-visible.
          if (!reasoningDeltaSeen && payload && typeof payload.text === 'string' && payload.text.trim()) {
            setPhase('thinking');
            send({ type: 'CUSTOM', name: 'reasoning.snapshot', value: { text: payload.text } });
          }
          break;

        case 'tool.start':
        case 'tool.started': {
          const toolName = (payload && payload.name) || (params.name) || params.tool || 'tool';
          const toolCallId = (payload && payload.tool_id) || params.tool_id || (payload && payload.toolCallId) || params.toolCallId || `tool-${toolName}`;
          const args = (payload && payload.args) ? payload.args : payload;
          toolStartTimes.set(toolCallId, Date.now());
          setPhase('tool_executing');
          emitToolStart(toolCallId, toolName, args, messageId);
          break;
        }

        case 'tool.chunk':
        case 'tool.output': {
          // Streamed tool output (e.g. terminal-style line-by-line results).
          const toolCallId = (payload && payload.tool_id) || params.tool_id || (payload && payload.toolCallId) || params.toolCallId || 'tool';
          const chunk = payload && (payload.chunk || payload.text || payload.line || '');
          if (chunk) {
            send({ type: 'CUSTOM', name: 'tool.chunk', value: { toolCallId, chunk } });
          }
          break;
        }

        case 'tool.complete':
        case 'tool.completed': {
          const toolName = (payload && payload.name) || (params.name) || params.tool || 'tool';
          const toolCallId = (payload && payload.tool_id) || params.tool_id || (payload && payload.toolCallId) || params.toolCallId || `tool-${toolName}`;
          const result = payload && (payload.result !== undefined ? payload.result : payload);
          const startTs = toolStartTimes.get(toolCallId);
          const durationMs = startTs ? Date.now() - startTs : undefined;
          toolStartTimes.delete(toolCallId);
          emitToolEnd(toolCallId, result, durationMs, false);
          // Inline diff emitted by some tools (e.g. file edits). Attach to the
          // tool card so the frontend can render a real diff instead of raw text.
          if (payload && payload.inline_diff) {
            send({ type: 'CUSTOM', name: 'tool.inline_diff', value: { toolCallId, diff: payload.inline_diff } });
          }
          setPhase('text_generating');
          break;
        }

        case 'tool.result': {
          const toolName = (payload && payload.name) || (params.name) || params.tool || 'tool';
          const toolCallId = (payload && payload.tool_id) || params.tool_id || (payload && payload.toolCallId) || params.toolCallId || `tool-${toolName}`;
          const result = payload && (payload.result !== undefined ? payload.result : payload);
          emitToolResult(toolCallId, result);
          break;
        }

        case 'tool.failed': {
          const toolName = (payload && payload.name) || (params.name) || params.tool || 'tool';
          const toolCallId = (payload && payload.tool_id) || params.tool_id || (payload && payload.toolCallId) || params.toolCallId || `tool-${toolName}`;
          const result = payload && (payload.error || payload.result !== undefined ? payload.result : payload);
          const startTs = toolStartTimes.get(toolCallId);
          const durationMs = startTs ? Date.now() - startTs : undefined;
          toolStartTimes.delete(toolCallId);
          emitToolEnd(toolCallId, result, durationMs, true);
          setPhase('text_generating');
          break;
        }

        // Agent terminal tabs: Hermes tui_gateway wires process_registry.on_output
        // → `agent.terminal.output` (raw ANSI chunks, keyed by process_id) and
        // on_close → `terminal.close`. Relay both so the frontend can render a
        // live xterm pane and drop a tab when the process is reaped.
        case 'agent.terminal.output': {
          const processId = (payload && (payload.process_id || payload.processId)) || '';
          const chunk = payload && (payload.chunk || payload.text || '');
          if (processId && chunk) {
            send({ type: 'CUSTOM', name: 'agent.terminal.output', value: { process_id: processId, chunk } });
          }
          break;
        }

        case 'terminal.close': {
          const processId = (payload && (payload.process_id || payload.processId)) || (params && (params.process_id || params.processId)) || '';
          send({ type: 'CUSTOM', name: 'terminal.close', value: { process_id: processId } });
          break;
        }

        case 'message.complete':
        case 'message.end': {
          const text = payload && (payload.text || payload.rendered);
          // Real per-run token / cost accounting from the backend. Forward so the
          // frontend can replace its character-estimate ContextUsage with real numbers.
          if (payload && payload.usage) {
            send({ type: 'CUSTOM', name: 'usage.update', value: payload.usage });
          }
          setPhase('idle');
          finalize(text);
          break;
        }

        case 'session.usage': {
          // Live cumulative usage pushes during a session.
          if (payload) {
            send({ type: 'CUSTOM', name: 'usage.update', value: payload });
          }
          break;
        }

        // Explicit /goal loop signals from the tui_gateway judge hook.
        // goal.continue → another round is queued (also resets the idle
        // timer naturally); goal.complete → judge said done/paused — the
        // waitForHermesTurn handler ends the SSE turn on this signal.
        case 'goal.continue':
          send({ type: 'CUSTOM', name: 'goal.continue', value: payload || {} });
          break;

        case 'goal.complete':
          send({ type: 'CUSTOM', name: 'goal.complete', value: payload || {} });
          break;

        case 'status.update': {
          const kind = payload && typeof payload.kind === 'string' ? payload.kind : '';
          let text = payload && typeof payload.text === 'string' ? payload.text : '';
          const isError = kind === 'error' || /❌|error|fail|timed out/i.test(text);
          if (isError) {
            hasRunError = true;
            send({ type: 'RUN_ERROR', runId: ctx.runId, message: text });
          } else {
            // status.update is a STATUS channel (compacting / loading / etc.),
            // NOT message content. Forward to the frontend StatusBar instead of
            // polluting the assistant message body.
            if (text) {
              send({ type: 'CUSTOM', name: 'status.update', value: { kind, text } });
            }
          }
          break;
        }

        case 'error': {
          hasRunError = true;
          const msg = payload && payload.message ? payload.message : String(payload || 'unknown error');
          send({ type: 'RUN_ERROR', runId: ctx.runId, message: msg });
          break;
        }

        // --- Contract layer (L5): progress / artifact / approval events ----
        // The backend emits these as Hermes events; translate them into AG-UI
        // CUSTOM events consumed by the generic WorkflowTimeline/ArtifactCard.
        case 'workflow.progress':
          send({ type: 'CUSTOM', name: 'workflow.progress', value: payload });
          break;
        case 'workflow.artifact':
          send({ type: 'CUSTOM', name: 'workflow.artifact', value: payload });
          break;
        case 'workflow.approval':
          send({ type: 'CUSTOM', name: 'workflow.approval', value: payload });
          break;
        case 'workflow.error':
          send({ type: 'CUSTOM', name: 'workflow.error', value: payload });
          break;
        case 'workflow.done':
          send({ type: 'CUSTOM', name: 'workflow.done', value: payload });
          break;
        case 'workflow.graph':
          // DAG topology for the live node-trace panel. value = { nodes, edges, totalEpisodes }.
          send({ type: 'CUSTOM', name: 'workflow.graph', value: payload });
          break;
        case 'workflow.trace':
          // Per-node execution trace. value = { node, status: running|done|pending, episode }.
          send({ type: 'CUSTOM', name: 'workflow.trace', value: payload });
          break;

        // --- Agent 自渲染 UI 组件层 ----
        // Hermes 的 render_ui tool 通过 emit 该事件，把结构化 UI 描述透传给前端
        // useAgentStream.handleCustom("ui.render")，由 GeneratedComponent 路由渲染。
        // payload 形状：{ blockId, type, props, replace?, appendPreview? }
        case 'ui.render': {
          if (payload && payload.type && payload.blockId) {
            send({ type: 'CUSTOM', name: 'ui.render', value: payload });
          }
          break;
        }

        // --- P1: 通知 / 状态 / 工具进度 / 子 agent / MOA / 后台任务 / 评审 ---
        case 'notification.show': {
          // { id?, key?, kind?, level?, text?, ttl_ms? }
          send({ type: 'CUSTOM', name: 'notification.show', value: payload || {} });
          break;
        }
        case 'notification.clear': {
          send({ type: 'CUSTOM', name: 'notification.clear', value: payload || {} });
          break;
        }

        case 'tool.progress': {
          // { name?, preview? } — transient tool progress line.
          if (payload && (payload.name || payload.preview)) {
            send({ type: 'CUSTOM', name: 'tool.progress', value: { name: payload.name, preview: payload.preview } });
          }
          break;
        }
        case 'tool.generating': {
          // { name? } — "model is generating tool call X".
          if (payload && payload.name) {
            send({ type: 'CUSTOM', name: 'tool.generating', value: { name: payload.name } });
          }
          break;
        }

        case 'browser.progress': {
          // Agent-driven browser activity (route B / pw_browser_* tools).
          // payload = { message, level }. Forward verbatim; the frontend mirrors
          // it as a live progress log inside the BrowserPanel.
          if (payload && payload.message) {
            send({ type: 'CUSTOM', name: 'browser.progress', value: { message: payload.message, level: payload.level || 'info' } });
          }
          break;
        }

        case 'subagent.spawn_requested':
        case 'subagent.start':
        case 'subagent.thinking':
        case 'subagent.tool':
        case 'subagent.progress':
        case 'subagent.complete': {
          // Subagent mirror events. Forward verbatim with the event name as the
          // CUSTOM name; the frontend upserts into a subagent list keyed by id.
          send({ type: 'CUSTOM', name: type, value: payload || {} });
          break;
        }

        case 'moa.reference': {
          // { count?, index?, label?, text? }
          send({ type: 'CUSTOM', name: 'moa.reference', value: payload || {} });
          break;
        }
        case 'moa.aggregating': {
          // { aggregator? }
          send({ type: 'CUSTOM', name: 'moa.aggregating', value: payload || {} });
          break;
        }

        case 'background.complete': {
          // { task_id, text } — a prompt.background task finished.
          send({ type: 'CUSTOM', name: 'background.complete', value: payload || {} });
          break;
        }
        case 'review.summary': {
          // { text? } — a code review / self-review summary.
          if (payload && payload.text) {
            send({ type: 'CUSTOM', name: 'review.summary', value: { text: payload.text } });
          }
          break;
        }

        default:
          // Forward anything else as a raw source event for debugging.
          send({ type: 'RAW', runId: ctx.runId, event: { type, params }, source: 'hermes' });
      }
    }

    return { handleEvent, finalize, hasError: () => hasRunError };
  }

  function waitForHermesTurn(client, hermesSessionId, ctx, res, encoder, timeoutMs = 120000, opts = {}) {
    // goal mode: the gateway emits multiple message.start/complete cycles.
    // The idle timer resets on EVERY hermes event, so it must comfortably
    // cover the longest gap with no events — the model's first-round thinking
    // and slow tool executions (e.g. browser navigation). 5s was too short:
    // a goal kickoff that spent >5s before the first event was misjudged as
    // "loop ended", closing SSE and leaving the agent running headless
    // (2026-08-26 "agent 莫名中断" root cause).
    //
    // 2026-08-26 (second incident): 30s was ALSO too short — the /goal judge
    // (evaluate_after_turn) is a synchronous reasoning-model LLM call between
    // rounds that can stay silent for 30-120s+ on large histories, so the SSE
    // died mid-loop ("goal 会中断"). The gateway now emits status.update
    // heartbeats during judging plus explicit goal.continue / goal.complete
    // signals; 180s here is only the last-resort backstop for a wedged loop.
    //
    // 2026-08-27 (third incident): 实测回合内静默——单 API 调用 49.9s、单
    // terminal 工具 167.9s —— 180s 兜底仍会误杀长命令。循环正常结束由
    // goal.complete 显式信号驱动，idle 抬到 600s 纯粹防脱轨。
    const { goalMode = false, idleMs = Number(process.env.ABC_AGUI_GOAL_IDLE_MS || 600000) } = opts;
    const translator = createTurnTranslator(res, encoder, { ...ctx, hermesSessionId }, { multiRound: goalMode });

    const promise = new Promise((resolve, reject) => {
      const turnStartedAt = Date.now();
      let lastBackendEventAt = Date.now();
      const timer = setTimeout(() => {
        cleanup();
        translator.finalize('');
        reject(new Error('Hermes turn timed out'));
      }, timeoutMs);

      let settled = false;
      // Phase 2: in goal mode the gateway emits multiple message.start/complete
      // cycles back-to-back. We can't resolve on the first complete — instead
      // arm an idle timer that resets on every event; when no new events
      // arrive for `idleMs`, the Ralph-style loop has ended.
      let idleTimer = null;
      const armIdleTimer = () => {
        if (!goalMode) return;
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => {
          if (settled) return;
          log('agui-server', `goal-mode idle ${idleMs}ms reached, ending turn`);
          cleanup();
          translator.finalize('');
          resolve();
        }, idleMs);
      };

      // Liveness heartbeat (2026-08-27 "卡住不知健不健康"): while a turn is
      // open the frontend gets a stream.heartbeat every 30s carrying how long
      // the BACKEND has been silent. Long terminal commands (600s hard cap)
      // and slow reasoning-model calls produce minutes of zero events — the
      // user had no way to tell "healthy but quiet" from "dead". This event
      // proves the SSE bridge is alive and quantifies the quiet; it does NOT
      // touch the goal idle timer (that tracks real backend events only).
      let hbTimer = setInterval(() => {
        if (settled) return;
        sendSSE(res, encoder, {
          type: 'CUSTOM',
          name: 'stream.heartbeat',
          value: {
            runId: ctx.runId,
            elapsedMs: Date.now() - turnStartedAt,
            sinceLastEventMs: Date.now() - lastBackendEventAt,
          },
        });
      }, 30000);

      const cleanup = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (hbTimer) { clearInterval(hbTimer); hbTimer = null; }
        if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
        client.off('event', handler);
        // Also remove disconnect listeners
        if (typeof client.off === 'function') {
          client.off('close', onDisconnect);
          client.off('disconnect', onDisconnect);
        }
      };

      const onDisconnect = () => {
        cleanup();
        translator.finalize('');
        reject(new Error('Hermes gateway disconnected'));
      };

      const handler = (type, params) => {
        // Reset idle timer on every event so a stream of events keeps the
        // SSE open. (Non-goal runs ignore the timer — first complete resolves.)
        armIdleTimer();
        lastBackendEventAt = Date.now();
        try {
          translator.handleEvent(type, params);
        } catch (err) {
          log('agui-server', `event translation error: ${err.message}`);
        }

        if (!goalMode && (type === 'message.complete' || type === 'message.end')) {
          cleanup();
          resolve();
          return;
        }
        if (goalMode && type === 'goal.complete') {
          // Judge declared the goal done/paused — end the turn gracefully
          // right away instead of letting the SSE hang until the idle
          // backstop fires (the gateway emits nothing further in that case,
          // so the frontend would sit at "工作中…" for minutes otherwise).
          log('agui-server', 'goal.complete received, ending goal turn');
          cleanup();
          translator.finalize('');
          resolve();
          return;
        }
        if (type === 'error') {
          cleanup();
          const payload = params && params.payload ? params.payload : params;
          const msg = payload && payload.message
            ? payload.message
            : (payload && typeof payload.text === 'string' ? payload.text : 'Hermes error');
          reject(new Error(msg));
          return;
        }
        if (type === 'status.update' && translator.hasError()) {
          // A fatal lifecycle status (e.g. provider 401) has already been
          // emitted as RUN_ERROR; finish the turn without sending RUN_FINISHED.
          cleanup();
          resolve();
        }
      };

      client.on('event', handler);
      // Detect gateway disconnect mid-turn so we don't hang until the 30-min timeout.
      if (typeof client.on === 'function') {
        client.on('close', onDisconnect);
        client.on('disconnect', onDisconnect);
      }
      // Arm the idle timer eagerly so a goal that emits nothing (e.g. budget
      // exhausted before first round) still terminates.
      armIdleTimer();
    });

    return { promise, translator };
  }

  // Safety net: if the gateway WebSocket hasn't connected yet (e.g. Hermes is
  // still starting), wait up to 5 seconds for it to come up instead of
  // immediately rejecting the request.  This covers edge cases where the
  // renderer obtains aguiPort before gatewayReady is true (e.g. cached
  // value, race between IPC events).
  function waitForGateway(client) {
    if (!client) return Promise.resolve(false);
    if (client.ready) return Promise.resolve(true);
    return new Promise((resolve) => {
      const onOpen = () => {
        client.off('open', onOpen);
        resolve(true);
      };
      client.once('open', onOpen);
      setTimeout(() => { client.off('open', onOpen); resolve(false); }, 5000);
    });
  }

  async function handleAgentConnect(req, res, encoder, ctx) {
    const client = gatewayClient();
    if (!client || !(await waitForGateway(client))) {
      sendSSE(res, encoder, { type: 'RUN_ERROR', message: 'Hermes gateway not connected' });
      return res.end();
    }

    sendSSE(res, encoder, { type: 'RUN_STARTED', threadId: ctx.threadId, runId: ctx.runId });

    try {
      await ensureHermesSession(client, ctx);
      // Replay any existing messages so CopilotKit's client state stays in sync.
      for (const m of ctx.messages) {
        const messageId = m.id || uuidv4();
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_START', messageId, role: m.role });
        if (m.content) {
          const text = typeof m.content === 'string' ? m.content : extractText(m.content);
          if (text) {
            sendSSE(res, encoder, { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: text });
          }
        }
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_END', messageId });
      }
      sendSSE(res, encoder, { type: 'RUN_FINISHED', threadId: ctx.threadId, runId: ctx.runId });
    } catch (err) {
      log('agui-server', `agent/connect error: ${err.message}`);
      sendSSE(res, encoder, { type: 'RUN_ERROR', message: err.message });
    }
    res.end();
  }

  function waitForSessionInfo(client, hermesSessionId, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error('agent initialization timed out'));
      }, timeoutMs);

      const cleanup = () => {
        clearTimeout(timer);
        client.off('event', handler);
      };

      const handler = (type, params) => {
        if (type !== 'session.info') return;
        const sid = params && (params.session_id || params.sid);
        if (sid && sid !== hermesSessionId) return;
        cleanup();
        resolve(params && params.payload ? params.payload : params);
      };

      client.on('event', handler);
    });
  }

  async function handleAgentRun(req, res, encoder, ctx) {
    const client = gatewayClient();
    if (!client || !(await waitForGateway(client))) {
      sendSSE(res, encoder, { type: 'RUN_ERROR', message: 'Hermes gateway not connected' });
      return res.end();
    }

    const userMsgs = (ctx.messages || []).filter((m) => m.role === 'user');
    const lastUser = userMsgs[userMsgs.length - 1];
    let text = lastUser && lastUser.content ? extractText(lastUser.content) : '';

    // @ mention protocol (spec §2): resolve which workflow this turn delegates
    // to via an explicit `mentions` array or an `@Name` token. Purely
    // manifest-driven — no keyword sniffing, no per-workflow mapping.
    const mentionTarget = resolveMentionDelegation(text, ctx.forwardedProps.mentions);
    let delegatedAgent = mentionTarget ? mentionTarget.id : null;

    // Phase 2: set to true by the /goal kickoff branch below; flows through
    // runOnce → waitForHermesTurn → createTurnTranslator so SSE stays open
    // across the goal's multi-round Ralph-style loop.
    let goalMode = false;

    // ── Slash command pre-dispatch (B 方案) ────────────────────────────
    // If the last user message is a slash command the 9120 gateway knows how
    // to handle, dispatch it directly. exec/prefill replies skip the LLM
    // entirely; send/skill/alias (e.g. /goal <text>, /retry, /steer, /queue,
    // /learn, /moa, alias) resolve to a follow-up `message` we submit as a
    // normal agent turn so goals get set and the loop begins. Unknown
    // commands already returned null above → fall through to the LLM.
    const slash = await trySlashDispatch(text, client, ctx);
    if (slash && (slash.kind === 'exec' || slash.kind === 'prefill')) {
      sendSSE(res, encoder, { type: 'RUN_STARTED', threadId: ctx.threadId, runId: ctx.runId });
      if (slash.output && slash.output.trim()) {
        const mid = 'slash-' + ctx.runId;
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_START', messageId: mid, role: 'assistant' });
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_CONTENT', messageId: mid, delta: slash.output });
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_END', messageId: mid });
      }
      sendSSE(res, encoder, { type: 'RUN_FINISHED', threadId: ctx.threadId, runId: ctx.runId });
      return res.end();
    }
    if (slash && slash.kind === 'send') {
      // Kickoff commands: surface the notice (if any) as a message, then
      // submit the resolved `message` as a normal agent turn. NOTE: the
      // multi-turn goal auto-loop visibility past the first turn is Phase 2
      // (needs a goal-complete event from the gateway + SSE keep-alive).
      if (slash.notice && slash.notice.trim()) {
        const nid = 'slash-notice-' + ctx.runId;
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_START', messageId: nid, role: 'assistant' });
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_CONTENT', messageId: nid, delta: slash.notice });
        sendSSE(res, encoder, { type: 'TEXT_MESSAGE_END', messageId: nid });
      }
      if (slash.message) {
        text = slash.message;
        delegatedAgent = null; // slash already resolves its own target
      }
      // Phase 2 plumbing: carry the /goal kickoff flag down to runOnce /
      // waitForHermesTurn / createTurnTranslator so the bridge keeps SSE
      // open across multiple Ralph-style goal iterations.
      goalMode = !!(slash.goalMode);
    }

    // A structured ContractForm / Workbench invoke carries agent_name explicitly
    // (built by the frontend from the manifest's input_schema). Resolve it so
    // the Python langgraph_agent tool streams workflow.* events back.
    const isStructuredInvoke =
      /langgraph_agent/.test(text) && /agent_name/.test(text);
    if (isStructuredInvoke) {
      const m = text.match(/"agent_name"\s*:\s*"([^"]+)"/);
      if (m) delegatedAgent = m[1];
    }

    let wfRunId = null;

    // @mention delegation (free text): instruct the model to invoke the
    // resolved agent with the raw text as input. Structured input
    // (mode/series_script/…) is the ContractForm/Workbench's responsibility —
    // the bridge never parses workflow-specific input.
    if (!isStructuredInvoke && delegatedAgent && text) {
      text = [
        '请调用 langgraph_agent 工具，参数如下：',
        '{',
        `  "agent_name": "${delegatedAgent}",`,
        `  "input": ${JSON.stringify(text)}`,
        '}',
        '不要解释、不要加载 skill、不要调用其它工具，直接发起 langgraph_agent 调用。',
      ].join('\n');
    }
    if (!text) {
      sendSSE(res, encoder, { type: 'RUN_STARTED', threadId: ctx.threadId, runId: ctx.runId });
      sendSSE(res, encoder, { type: 'RUN_FINISHED', threadId: ctx.threadId, runId: ctx.runId });
      return res.end();
    }

    // Open the workflow-event subscriber and publish the workflowRunId so the
    // Python langgraph_agent tool can forward progress/artifact/done events
    // back to this SSE stream via HTTP POST /api/ag-ui/workflow-event.
    // Always register so that ANY langgraph_agent call — whether triggered by
    // a ContractForm, @mention, or the model's own tool-use decision — gets
    // verbose progress forwarding.
    wfRunId = 'wf-' + ctx.runId;
    workflowSubscribers.set(wfRunId, (obj) => sendSSE(res, encoder, obj));
    // 回放订阅注册前已缓冲的事件（重连/竞态兜底）：早到的 workflow.started 会
    // 在这里被补发并正确置 keepOpen，避免后台 run 的 SSE 被提前关闭。
    drainEventBuffer(wfRunId, (obj) => sendSSE(res, encoder, obj));
    // 登记后台 run 句柄（keepOpen 初始 false，收到 workflow.started 后置 true）
    backgroundRuns.set(wfRunId, {
      wfRunId, res, encoder, ctx,
      keepOpen: false, ended: false, timer: null,
    });
    try {
      const base = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
      const dir = path.join(base, 'workflow_hitl');
      fs.mkdirSync(dir, { recursive: true });
        // Per-run coordination file so concurrent workflows don't overwrite
        // Per-run coordination file so concurrent workflows don't overwrite
        // each other. Python side globs for .wf_active_*.json.
        const coordFile = path.join(dir, `.wf_active_${ctx.runId}.json`);
        fs.writeFileSync(
          coordFile,
          JSON.stringify({ runId: wfRunId, threadId: ctx.threadId }),
          'utf-8'
        );
        log('agui-server', `registered workflow-event subscriber ${wfRunId} → ${coordFile}`);
      } catch (e) {
        log('agui-server', `workflow coord write failed: ${e.message}`);
      }

    sendSSE(res, encoder, { type: 'RUN_STARTED', threadId: ctx.threadId, runId: ctx.runId });

    // Register this turn's SSE sender so render_ui (and any future agent-driven
    // UI) can push ui.render events back from the Hermes process over HTTP.
    const turnSend = (obj) => sendSSE(res, encoder, obj);
    activeTurnSenders.set(ctx.runId, turnSend);
    // 回放该 runId 缓冲的 ui.render 事件（重连/竞态兜底）。
    drainEventBuffer(ctx.runId, turnSend);
    writeUiActiveCoord(ctx.runId, ctx.threadId);

    async function getReadyHermesSession() {
      const { id: hermesSessionId, created: sessionCreated } = await ensureHermesSession(client, ctx);
      // Hermes creates sessions lazily: wait for the agent build to finish
      // before submitting the first prompt, otherwise prompt.submit fails with
      // "agent initialization timed out".
      if (sessionCreated) {
        await waitForSessionInfo(client, hermesSessionId, 60000);
      }
      return hermesSessionId;
    }

    // ── Native vision: queue this turn's images before the prompt ──────────
    // The renderer strips inline `data:image/...;base64,` URLs out of the
    // message text (sending that as text both blinds the model and blows the
    // context) and ships the bytes here instead. `image.attach_bytes` writes
    // them into the gateway's images dir and appends them to the session's
    // `attached_images` queue, which the very next `prompt.submit` drains into
    // provider-native `image_url` content parts. This is exactly the path the
    // Hermes TUI uses, so nothing provider-specific lives in this file.
    async function attachTurnImages(hermesSessionId) {
      const imgs = Array.isArray(ctx.images) ? ctx.images : [];
      if (imgs.length === 0) return;
      for (let i = 0; i < imgs.length; i++) {
        const img = imgs[i] || {};
        const b64 =
          typeof img === 'string'
            ? img
            : img.dataUrl || img.data || img.content_base64 || '';
        if (!b64) continue;
        try {
          const r = await client.request(
            'image.attach_bytes',
            {
              session_id: hermesSessionId,
              content_base64: b64,
              filename: img.filename || `image_${i + 1}.png`,
            },
            60000
          );
          log('agui-server', `image attached: ${(r && r.path) || '?'} (${(r && r.bytes) || 0}B)`);
        } catch (e) {
          // A bad image must not kill the turn — the text still goes through.
          log('agui-server', `image.attach_bytes failed: ${(e && e.message) || e}`);
        }
      }
    }

    async function runOnce(hermesSessionId, goalMode = false) {
      // The turn can stay open for a long time when a workflow pauses at a
      // human-in-the-loop approval gate (the graph waits on the control-file
      // channel until the user decides). Default to 30 min; override via env.
      //
      // /goal 模式的硬超时必须按"整个 Ralph 循环"计量而不是单回合：
      // 2026-08-26 实测一个 20-turn 预算的 goal 跑了 42 分钟（23:09→23:51），
      // 30 分钟硬超时会在循环中段把 SSE 掐掉。goal 的优雅收尾由显式
      // goal.complete 信号负责，硬超时只是防脱轨兜底，给到 6 小时。
      const turnTimeoutMs = goalMode
        ? Number(process.env.ABC_AGUI_GOAL_TURN_TIMEOUT || 6 * 60 * 60 * 1000)
        : Number(process.env.ABC_AGUI_TURN_TIMEOUT || 1800000);
      // Phase 2: when a /goal kickoff is in flight, the gateway recurses into
      // multiple message.start/complete cycles (Ralph-style loop). The
      // translator must mint a fresh messageId each round and the turn waiter
      // must NOT resolve on the first `message.complete` — use an idle timer
      // instead so subsequent rounds keep flowing into SSE.
      const waitOpts = goalMode
        // idleMs 必须与 waitForHermesTurn 的默认值保持一致。
        // 2026-08-27 两连击：① 这里曾硬编码 30000，覆盖了函数内改过的
        // 180000 默认值；② 实测 goal 回合内的真实静默间隔：单个 API 调用
        // 49.9s（reasoning 不流式时）、单个 terminal 工具 167.9s（长命令
        // 无 chunk）——180s 兜底仍可能误杀，抬到 600s。循环的正常结束
        // 由 goal.complete / goal.continue 显式信号驱动，idle 只是兜底。
        ? { goalMode: true, idleMs: Number(process.env.ABC_AGUI_GOAL_IDLE_MS || 600000) }
        : undefined;
      const { promise: turnPromise, translator } = waitForHermesTurn(
        client, hermesSessionId, ctx, res, encoder, turnTimeoutMs, waitOpts
      );
      await attachTurnImages(hermesSessionId);
      let wechatLine = '';
      let appLines = [];
      try { wechatLine = buildWechatEnvLine(options); } catch (_) { wechatLine = ''; }
      try { appLines = getAppContextLines(); } catch (_) { appLines = []; }
      await client.request('prompt.submit', {
        session_id: hermesSessionId,
        text: prependEnvContext(ctx, text, { wechatLine, appLines }),
      }, 120000);
      await turnPromise;
      return translator;
    }

    try {
      let hermesSessionId = await getReadyHermesSession();
      let translator;
      try {
        translator = await runOnce(hermesSessionId, goalMode);
      } catch (firstErr) {
        const msg = firstErr && firstErr.message ? firstErr.message : String(firstErr);
        log('agui-server', `prompt.submit error: ${msg}`);
        // If the session disappeared (e.g. Hermes restarted), clear the stale
        // mapping and recreate the session once.
        if (msg.toLowerCase().includes('session not found')) {
          log('agui-server', `clearing stale thread mapping ${ctx.threadId} -> ${hermesSessionId}`);
          await storage.setThreadMapping(ctx.threadId, null);
          hermesSessionId = await getReadyHermesSession();
          translator = await runOnce(hermesSessionId, goalMode);
        } else {
          throw firstErr;
        }
      }
      const br = backgroundRuns.get(wfRunId);
      if (br && br.keepOpen) {
        // 后台 workflow 已启动：保持 SSE 打开，等 workflow.done 再关闭。
        // 关键修复（2026-08-23）：此前用「盲 60 分钟定时器」强制关 SSE 并删除
        // 协调文件，会把「仍在 HITL 审批门等待真人决策」的后台 run 一起杀掉——
        // Python 侧 _wait_for_decision 仍在轮询决策文件，但 SSE 已被关、前端
        // approval 弹窗变成孤儿，用户回来点确认写出的决策文件无人消费（静默
        // 死锁）。现改为「被动遗弃定时器」：只在「超长一段时间（默认 24h）内
        // 完全没有任何事件」时才判定为真正遗弃并清理；活跃（含 HITL 等待中）
        // 的 run 永不被盲杀。协调文件也只在 done/真遗弃时删除。
        log('agui-server', `keeping SSE open for background run ${wfRunId}`);
        const ABANDON_MS = Number(process.env.ABC_BACKGROUND_RUN_ABANDON || 86400000); // 默认 24 小时
        br.keepAliveAt = Date.now();
        br.timer = setInterval(() => {
          const since = Date.now() - (br.keepAliveAt || 0);
          if (since >= ABANDON_MS) {
            log('agui-server', `background run ${wfRunId} abandoned (no events for ${ABANDON_MS}ms), forcing close`);
            clearInterval(br.timer);
            finishBackgroundRun(br);
          }
          // 否则保持打开：run 仍在跑（或在 HITL 审批门等待真人），绝不误杀。
        }, 60000);
        return; // 挂起等待 workflow.done，不在这里 end SSE
      }
      if (translator && !translator.hasError()) {
        sendSSE(res, encoder, { type: 'RUN_FINISHED', threadId: ctx.threadId, runId: ctx.runId });
      }
    } catch (err) {
      log('agui-server', `agent/run error: ${err.message}`);
      sendSSE(res, encoder, { type: 'RUN_ERROR', message: err.message });
    }
    // 正常（非后台）清理；后台 run 由 finishBackgroundRun 在 workflow.done 时清理。
    const br2 = backgroundRuns.get(wfRunId);
    if (!(br2 && br2.keepOpen)) {
      if (wfRunId) {
        workflowSubscribers.delete(wfRunId);
        backgroundRuns.delete(wfRunId);
        clearEventBuffer(wfRunId);
        try {
          const base = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
          const coordFile = path.join(base, 'workflow_hitl', `.wf_active_${ctx.runId}.json`);
          if (fs.existsSync(coordFile)) fs.unlinkSync(coordFile);
        } catch (_) { /* best-effort */ }
      }
      activeTurnSenders.delete(ctx.runId);
      clearEventBuffer(ctx.runId);
      clearUiActiveCoord();
      res.end();
    }
  }

  async function handleAgentStop(req, res, ctx) {
    const client = gatewayClient();
    const hermesSessionId = await storage.getThreadMapping(ctx.threadId);
    log('agui-server', `agent/stop requested for ${ctx.threadId} -> ${hermesSessionId || 'unknown'}`);
    if (client && client.ready && hermesSessionId) {
      try {
        // Hermes queues/interrupts atomically: if a turn is running the
        // interrupt aborts it; if one is queued it is dropped.
        await client.request('session.interrupt', { session_id: hermesSessionId }, 10000);
        log('agui-server', `session.interrupt sent for ${hermesSessionId}`);
      } catch (err) {
        log('agui-server', `session.interrupt failed: ${err.message}`);
      }
    }
    return res.json({ status: 'ok' });
  }

  async function handleLegacyRun(req, res, encoder, ctx) {
    return handleAgentRun(req, res, encoder, ctx);
  }

  app.post('/api/ag-ui/run', async (req, res) => {
    const body = req.body || {};
    log('agui-server', `run request body keys: ${Object.keys(body).join(', ')}`);
    log('agui-server', `run request body: ${JSON.stringify(body).slice(0, 2000)}`);

    if (body.method === 'info') {
      try {
        return res.json(await buildInfoResponse());
      } catch (err) {
        log('agui-server', `info error: ${err.message}`);
        return res.json({ status: 'ok', runtime: 'abcyesno', runtimeVersion: '1.0.0', agents: {}, defaultAgent: 'default' });
      }
    }

    const ctx = resolveRunContext(body);
    const encoder = new EventEncoder({ accept: req.headers.accept || '*/*' });
    res.setHeader('Content-Type', encoder.getContentType());
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    switch (ctx.method) {
      case 'agent/connect':
        return handleAgentConnect(req, res, encoder, ctx);
      case 'agent/run':
        return handleAgentRun(req, res, encoder, ctx);
      case 'agent/stop':
        return handleAgentStop(req, res, ctx);
      default:
        return handleLegacyRun(req, res, encoder, ctx);
    }
  });

  // --- Workflow HITL event relay (contract L5) -------------------------
  // The Python langgraph runtime cannot reach the Node SSE closure, so its
  // on_event callback POSTs workflow.* events here. We relay each one to the
  // SSE subscriber registered for the run that spawned the workflow.
  app.post('/api/ag-ui/ui-event', (req, res) => {
    // Agent 自渲染 UI 组件桥：Hermes 的 render_ui tool 在模型调用点把结构化 UI
    // 描述 POST 过来，我们中继成 AG-UI CUSTOM{name:"ui.render"} 注入当前对话轮的 SSE 流。
    try {
      const body = req.body || {};
      const runId = body.runId;
      const payload = body.payload || {};
      if (!runId) {
        return res.json({ status: 'error', message: 'runId is required' });
      }
      const send = activeTurnSenders.get(runId);
      if (!send) {
        bufferEvent(runId, { type: 'CUSTOM', name: 'ui.render', value: payload });
        log('agui-server', `ui-event buffered (no active turn) for ${runId}`);
        return res.json({ status: 'buffered', runId });
      }
      // 透传白名单已在 useAgentStream 侧校验；这里只保证必要字段存在。
      if (!payload || !payload.type || !payload.blockId) {
        return res.json({ status: 'error', message: 'payload{type, blockId} required' });
      }
      send({ type: 'CUSTOM', name: 'ui.render', value: payload });
      log('agui-server', `relayed ui.render(${payload.type}) -> ${runId}`);
      res.json({ status: 'ok' });
    } catch (err) {
      log('agui-server', `ui-event relay failed: ${err.message}`);
      res.json({ status: 'error', message: err.message });
    }
  });

  app.post('/api/ag-ui/workflow-event', (req, res) => {
    try {
      const body = req.body || {};
      const runId = body.runId || body.workflowRunId;
      // Normalize the event type. The Python emitter sends the FULL name
      // ("workflow.started", "workflow.progress", "workflow.done", ...) as
      // `type`, while the Node bridge expects the bare suffix ("started",
      // "progress", ...) and adds the `workflow.` prefix itself when framing
      // the AG-UI CUSTOM event name. If we don't strip the prefix here we get
      // a doubled "workflow.workflow.started" name AND the `eventType ===
      // 'started'` keep-open hook below never fires — which silently closed the
      // SSE before the background run finished (symptom: "buffered (no
      // subscriber)"). Accept both forms for safety.
      const rawType = body.type || '';
      const eventType = rawType.startsWith('workflow.') ? rawType.slice('workflow.'.length) : rawType;
      const payload = body.payload || {};
      if (!runId || !eventType) {
        return res.json({ status: 'error', message: 'runId and type are required' });
      }
      const send = workflowSubscribers.get(runId);
      if (!send) {
        // 订阅者尚未注册 / SSE 断开重连窗口：缓冲而非丢弃，待订阅者注册时回放。
        bufferEvent(runId, { type: 'CUSTOM', name: `workflow.${eventType}`, value: payload });
        log('agui-server', `workflow-event buffered (no subscriber) for ${runId}`);
        return res.json({ status: 'buffered', runId });
      }
      send({ type: 'CUSTOM', name: `workflow.${eventType}`, value: payload });
      // 任何事件到达都刷新「最后活跃时间」，被动遗弃定时器据此判断 run 是否
      // 仍存活（含 HITL 审批门等待中——Python 每 0.5s 轮询决策文件，但并
      // 不向本通道发事件；此时靠 below 的 HITL 保活心跳兜底，见 interrupt 路由）。
      const brLive = backgroundRuns.get(runId);
      if (brLive) brLive.keepAliveAt = Date.now();
      // 后台 run 生命周期钩子（contract B）：
      //  started → 标记本路 SSE 保持打开，直到 done 才关闭；
      //  done    → 关闭并保持打开的 SSE（清理 subscriber / coord / end）。
      if (eventType === 'started') {
        const br = backgroundRuns.get(runId);
        if (br) br.keepOpen = true;
        log('agui-server', `background run marked keep-open: ${runId}`);
      } else if (eventType === 'done') {
        const br = backgroundRuns.get(runId);
        if (br) {
          if (br.timer) clearInterval(br.timer);
          finishBackgroundRun(br);
        }
      }
      log('agui-server', `relayed workflow.${eventType} -> ${runId}`);
      res.json({ status: 'ok' });
    } catch (err) {
      log('agui-server', `workflow-event relay failed: ${err.message}`);
      res.json({ status: 'error', message: err.message });
    }
  });

  // --- Human-in-the-loop control channel (contract L4) -------------------
  // The LangGraph runtime (paused at an interrupt()) polls a decision file
  // under HERMES_HOME/workflow_hitl/<runId>.json. The frontend brake (ApprovalDialog)
  // POSTs the decision here; we write it atomically so the waiting graph resumes.
  app.post('/api/ag-ui/interrupt', (req, res) => {
    try {
      const body = req.body || {};
      const runId = body.workflowRunId || body.runId;
      const decision = body.decision || 'approve';
      const steerText = body.steerText || '';
      if (!runId) {
        return res.json({ status: 'error', message: 'workflowRunId is required' });
      }
      // 关键修复（2026-08-23）：若该 run 的 SSE 订阅者已不存在（后台 run 已
      // 结束/被遗弃、协调文件已删），则拒绝写入决策文件并返回显式错误，让
      // 前端把 approval 弹窗改成「已失效/任务已结束」状态，而不是静默写出一个
      // 无人消费的决策文件（旧 bug：用户点确认毫无反应）。
      if (!workflowSubscribers.has(runId)) {
        log('agui-server', `interrupt rejected for dead run ${runId} (no subscriber)`);
        return res.json({ status: 'error', message: '该审批任务已结束或已超时，请重新发起任务。', code: 'RUN_ENDED' });
      }
      // 心跳保活：用户在 HITL 审批门等待期间，Python 只轮询决策文件、不向本
      // 通道发任何 workflow.* 事件。刷新 keepAliveAt 防止被动遗弃定时器把
      // 一个「只是等人确认」的活跃 run 误判为遗弃。
      const brHeart = backgroundRuns.get(runId);
      if (brHeart) brHeart.keepAliveAt = Date.now();

      const base = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
      const dir = path.join(base, 'workflow_hitl');
      fs.mkdirSync(dir, { recursive: true });
      const file = path.join(dir, `${runId}.json`);
      const tmp = path.join(dir, `.${runId}.${process.pid}.tmp`);
      fs.writeFileSync(tmp, JSON.stringify({ decision, steerText, ts: Date.now() }), 'utf-8');
      fs.renameSync(tmp, file); // atomic replace
      log('agui-server', `wrote HITL decision ${decision} for ${runId}`);
      res.json({ status: 'ok' });
    } catch (err) {
      log('agui-server', `interrupt write failed: ${err.message}`);
      res.json({ status: 'error', message: err.message });
    }
  });

  // --- Voice STT proxy (Composer -> Agnes /audio/transcriptions) ------
  // The browser records audio and ships it as base64; we decode, then call
  // Agnes server-side so the API key never reaches the renderer. Agnes is
  // OpenAI-compatible Whisper, so multipart form-data with `file` works.
  function readAgnesApiKey() {
    const home = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
    try {
      const text = fs.readFileSync(path.join(home, '.env'), 'utf-8');
      const m = text.match(/^AGNES_API_KEY=(.+)$/m);
      if (m) return m[1].trim();
    } catch (_) {}
    return process.env.AGNES_API_KEY || '';
  }

  // --- WeChat notify bridge (agent → bound WeChat user) -------------------
  // The agent learns about this endpoint from the env context line (see
  // buildWechatEnvLine). It POSTs {"text": "..."} and we forward to the
  // wechat bridge's send (sendTestMessage). The bridge enforces its own
  // constraints (must be connected; iLink passive-reply window), surfacing
  // a plain {ok, error} result the agent can relay back to the user.
  app.post('/api/wechat/notify', async (req, res) => {
    try {
      const body = req.body || {};
      const text = String(body.text || '').trim();
      if (!text) {
        return res.json({ ok: false, error: 'text is required' });
      }
      const notify = options && typeof options.wechatNotify === 'function'
        ? options.wechatNotify : null;
      if (!notify) {
        return res.json({ ok: false, error: 'wechat bridge unavailable' });
      }
      // 单条上限 2000 字符，防止 agent 把整份产物当通知发出去。
      const result = await notify(text.slice(0, 2000));
      log('agui-server', `wechat.notify → ${JSON.stringify(result).slice(0, 200)}`);
      return res.json(result);
    } catch (err) {
      log('agui-server', `wechat.notify error: ${err && err.message}`);
      return res.json({ ok: false, error: (err && err.message) || String(err) });
    }
  });

  app.post('/api/transcribe', async (req, res) => {
    try {
      const body = req.body || {};
      const audio = body.audio;
      const mime = body.mime || 'audio/webm';
      if (!audio) {
        return res.json({ error: 'audio is required' });
      }
      const key = readAgnesApiKey();
      if (!key) {
        return res.json({ error: 'AGNES_API_KEY 未配置' });
      }
      const buf = Buffer.from(audio, 'base64');
      const blob = new Blob([buf], { type: mime });
      const fd = new FormData();
      const ext = mime.includes('wav') ? 'wav'
        : (mime.includes('mp4') || mime.includes('m4a')) ? 'm4a'
        : 'webm';
      fd.append('file', blob, `audio.${ext}`);
      fd.append('model', 'agnes-2.5-flash');
      const upstream = await fetch('https://apihub.agnes-ai.com/v1/audio/transcriptions', {
        method: 'POST',
        headers: { Authorization: `Bearer ${key}` },
        body: fd,
      });
      const data = await upstream.json().catch(() => ({}));
      if (!upstream.ok) {
        return res.json({ error: `STT HTTP ${upstream.status}: ${JSON.stringify(data).slice(0, 200)}` });
      }
      const text = typeof data === 'string' ? data : (data.text || data.transcript || '');
      return res.json({ text: String(text) });
    } catch (err) {
      log('agui-server', `transcribe failed: ${err.message}`);
      return res.json({ error: err.message });
    }
  });

  // ── TTS: generate speech via edge-tts (Microsoft cloud), return mp3 base64 ──
  // Mirrors /api/transcribe: frontend calls window.hermes.synthesizeSpeech →
  // this route → edge-tts → mp3 buffer (base64). No API key needed (free
  // Microsoft Edge endpoint), but requires network access.
  function splitTextForTts(text, max) {
    const out = [];
    let cur = '';
    const parts = text.split(/(?<=[。！？!?；;\n])/);
    for (const p of parts) {
      if ((cur + p).length > max && cur) {
        out.push(cur);
        cur = '';
      }
      cur += p;
    }
    if (cur) out.push(cur);
    return out.length ? out : [text.slice(0, max)];
  }

  app.post('/api/tts', async (req, res) => {
    try {
      const body = req.body || {};
      const text = typeof body.text === 'string' ? body.text : '';
      if (!text.trim()) {
        return res.json({ error: 'text is required' });
      }
      const voice = body.voice || 'zh-CN-XiaoxiaoNeural';
      const rate = Number(body.rate);
      // edge-tts rate is a percentage string: "+0%" (default), "-50%" (half),
      // "+100%" (double). Map our 0.5–2.0 multiplier onto that range.
      let rateStr = '+0%';
      if (Number.isFinite(rate) && rate > 0) {
        const pct = Math.round((rate - 1) * 100);
        rateStr = (pct >= 0 ? '+' : '') + pct + '%';
      }
      const chunks = splitTextForTts(text, 7000);
      const buffers = [];
      for (const c of chunks) {
        const b = await edgeTtsSynth(c, voice, rateStr);
        buffers.push(Buffer.isBuffer(b) ? b : Buffer.from(b));
      }
      const buf = Buffer.concat(buffers);
      return res.json({ audio: buf.toString('base64'), mime: 'audio/mpeg' });
    } catch (err) {
      log('agui-server', `tts failed: ${err.message}`);
      return res.json({ error: err.message });
    }
  });

  // ── Auto-generate a short, summarized session title from the first
  //    exchange. Replaces the old "first N chars" hack with a real model
  //    summary so the header / sidebar don't show raw message fragments.
  //    Failures return an empty title and the caller keeps its fallback. ──
  app.post('/api/session-title', async (req, res) => {
    try {
      const body = req.body || {};
      const userText = String(body.userText || '').slice(0, 800);
      const assistantText = String(body.assistantText || '').slice(0, 1500);
      if (!userText && !assistantText) return res.json({ title: '' });
      const key = readAgnesApiKey();
      if (!key) return res.json({ title: '' });
      const sys = '你是会话标题生成器。根据一段对话（用户提问与助手回答），用中文生成一个不超过 12 字的简洁标题，概括对话主题。只输出标题本身，不要标点、不要引号、不要解释、不要换行。';
      const user = `用户：${userText}\n助手：${assistantText}`;
      const upstream = await fetch('https://apihub.agnes-ai.com/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
        body: JSON.stringify({
          model: 'agnes-2.5-flash',
          messages: [
            { role: 'system', content: sys },
            { role: 'user', content: user },
          ],
          max_tokens: 48,
          temperature: 0.3,
        }),
      });
      const data = await upstream.json().catch(() => ({}));
      let title = '';
      const msg = data && data.choices && data.choices[0] && data.choices[0].message;
      if (msg) title = String(msg.content || '');
      title = title.replace(/["'"'"'""''「」【】\s]/g, '').trim();
      if (title.length > 24) title = title.slice(0, 24);
      return res.json({ title });
    } catch (err) {
      log('agui-server', `session-title failed: ${err.message}`);
      return res.json({ title: '' });
    }
  });

  // ── Studio workbench: real Agnes image generation ──────────────────────
  app.post('/api/studio/generate-image', async (req, res) => {
    try {
      const body = req.body || {};
      const prompt = body.prompt;
      if (!prompt) return res.json({ ok: false, error: 'prompt 必填' });
      const url = await agnes.generateImage({
        prompt,
        size: body.size || '2K',
        ratio: body.ratio || '1:1',
      });
      return res.json({ ok: true, url });
    } catch (e) {
      log('agui-server', `studio image failed: ${e.message}`);
      return res.json({ ok: false, error: e.message });
    }
  });

  // ── Studio workbench: real Agnes video generation (async poll) ─────────
  app.post('/api/studio/generate-video', async (req, res) => {
    try {
      const body = req.body || {};
      const prompt = body.prompt;
      if (!prompt) return res.json({ ok: false, error: 'prompt 必填' });
      const url = await agnes.generateVideo({
        prompt,
        image: body.image || undefined,
        keyframes: body.keyframes || undefined,
        reference_images: body.reference_images || undefined,
        width: body.width || 1152,
        height: body.height || 768,
        num_frames: body.num_frames || 81,
        frame_rate: body.frame_rate || 24,
        seconds: body.seconds || undefined,
        aspect_ratio: body.aspect_ratio || undefined,
      });
      return res.json({ ok: true, url });
    } catch (e) {
      log('agui-server', `studio video failed: ${e.message}`);
      return res.json({ ok: false, error: e.message });
    }
  });

  // ── Studio workbench: export Jianying draft with real downloaded media ──
  app.post('/api/studio/prepare-export', async (req, res) => {
    try {
      const body = req.body || {};
      const project = body.project || {};
      const timeline = Array.isArray(body.timeline) ? body.timeline : [];
      const shotCfg = body.shotCfg || {};
      const shots = Array.isArray(body.shots) ? body.shots : [];
      if (!timeline.length) return res.json({ ok: false, error: '时间轴为空' });

      const resStr = String(project.res || '1080x1920');
      const resMatch = resStr.match(/(\d{3,5})\s*[x×*]\s*(\d{3,5})/);
      const w = resMatch ? parseInt(resMatch[1], 10) : 1080;
      const h = resMatch ? parseInt(resMatch[2], 10) : 1920;
      const fps = Number(project.fps || 30) || 30;
      const safeName = String(project.name || 'short_drama').replace(/[^\w一-龥-]/g, '_');

      const home = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
      const draftDir = path.join(home, 'studio_exports', `${safeName}.draft`);
      const matsDir = path.join(draftDir, 'materials');
      fs.mkdirSync(matsDir, { recursive: true });

      const videos = [];
      const images = [];
      const segments = [];
      let start = 0;
      let idx = 0;
      for (const k of timeline) {
        const cfg = shotCfg[k] || { dur: 4, trans: 'none' };
        const dur = Math.max(1, cfg.dur) * 1000000; // microseconds
        const sh = shots.find((x) => x.key === k) || {};
        idx += 1;
        const id = `m${idx}`;
        let materialId = null;
        if (sh.videoUrl) {
          const dest = await agnes.downloadMedia(sh.videoUrl, matsDir, `shot_${k}`);
          videos.push({ id, path: path.relative(draftDir, dest).replace(/\\/g, '/'), duration: dur });
          materialId = id;
        } else if (sh.imgUrl) {
          const dest = await agnes.downloadMedia(sh.imgUrl, matsDir, `shot_${k}`);
          images.push({ id, path: path.relative(draftDir, dest).replace(/\\/g, '/'), duration: dur });
          materialId = id;
        } else {
          continue; // skip shots without any generated media
        }
        const seg = { material_id: materialId, target_timerange: { start, duration: dur } };
        if (cfg.trans && cfg.trans !== 'none') seg.transition = { type: cfg.trans };
        segments.push(seg);
        start += dur;
      }
      if (!segments.length) {
        return res.json({ ok: false, error: '没有可导出的素材（请先在「分镜」页生成图/视频）' });
      }

      const draft = {
        app_version: '5.0.0',
        fps,
        width: w || 1080,
        height: h || 1920,
        version: '1.0.0',
        materials: { videos, images, audios: [], texts: [], transitions: [] },
        tracks: [{ type: 'video', id: 't1', segments }],
      };
      fs.writeFileSync(path.join(draftDir, 'draft_content.json'), JSON.stringify(draft, null, 2), 'utf-8');
      fs.writeFileSync(
        path.join(draftDir, 'draft_meta.json'),
        JSON.stringify({ app_version: '5.0.0', platform: 'pc', project: 'draft', tm: Date.now() }, null, 2),
        'utf-8'
      );

      return res.json({
        ok: true,
        draftDir,
        count: segments.length,
        totalSec: start / 1000000,
        json: draft,
      });
    } catch (e) {
      log('agui-server', `studio export failed: ${e.message}`);
      return res.json({ ok: false, error: e.message });
    }
  });

  return app;
}

function extractText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part;
        if (part && part.type === 'text') return part.text || '';
        return '';
      })
      .join('');
  }
  return '';
}

// ── Environment context prefill ──────────────────────────────────────────
// Hermes' assistant-level system prompt is static, so the model has no real
// time source and hallucinates dates ("206 年 8 月 5 日" ...) when asked.
// agui-server only forwards the `text` payload to `prompt.submit`, so we
// prepend a compact environment block (real current time + honesty rule +
// anti self-correction) right before submission. The WeChat bridge already
// injects a similar block in provider.ts; this is the canonical entry point
// so the main-app session window also benefits.
//
// Idempotent: if the user text already starts with our marker, skip (so a
// retry of the same message won't stack the prefix). Any client may opt
// out via `forwardedProps.env_aware === false`.
function formatNowForModel() {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
    const d = new Intl.DateTimeFormat('zh-CN', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'long',
    });
    const t = new Intl.DateTimeFormat('zh-CN', {
      timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false,
    });
    return `${d.format(new Date())} ${t.format(new Date())} (${tz})`;
  } catch {
    return new Date().toISOString();
  }
}
// ── WeChat bridge awareness ──────────────────────────────────────────────
// The wechat bridge daemon lives in the Electron main process and is invisible
// to the agent: without an explicit line in the env context the model answers
// "Hermes 没有配置微信" even when an account is bound and connected. We build a
// compact status line from the bridge status snapshot injected by main.js
// (options.getWechatStatus) and, when connected, expose the notify endpoint so
// the agent can actually push a message to the bound WeChat user.
function buildWechatEnvLine(opts) {
  let st = null;
  try {
    const get = opts && opts.getWechatStatus;
    if (typeof get === 'function') st = get();
  } catch { st = null; }
  if (!st || !st.bound) {
    return '- 微信通知：当前未绑定微信账号。若用户要求"微信通知我/发微信"，' +
      '告知可在 设置 → 微信绑定 中扫码绑定；不要回答"Hermes 没有配置微信"。';
  }
  if (st.state !== 'connected') {
    return `- 微信通知：已绑定微信账号${st.accountMasked ? `（${st.accountMasked}）` : ''}，` +
      `但桥接当前状态为 ${st.state}，暂时无法发送微信消息。`;
  }
  const port = (opts && opts.aguiPort) || process.env.AGUI_PORT || '9121';
  return '- 微信通知：微信桥已连接' + (st.accountMasked ? `（账号 ${st.accountMasked}）` : '') + '。' +
    '用户要求"微信通知我/完成后发微信"时，调用 wechat_notify 工具（参数 text）。' +
    '严禁用终端 curl 发微信——cmd 的 GBK 代码页会把命令行里的中文绞成乱码' +
    '（2026-08-27 实测）。' +
    '注意：微信机器人只能被动回复（窗口随用户每条来消息刷新、几分钟内过期）。' +
    '若工具返回 queued=true，说明通知已排队不会丢——告知用户随便给机器人发一条微信（如「1」）即可立即补发；' +
    '其他失败把 error 原样告知用户并说明原因。';
}

// ── App self-awareness block (data-driven) ───────────────────────────────
// The agent runs inside the Abcyesno desktop UI, but the backend runtime has
// no intrinsic knowledge of that — without these lines it behaves like a bare
// headless runtime ("我不知道自己在什么应用里"). This block gives it a compact
// model of the app it lives in: what the user sees, which UI-level tools
// exist, and where the other capability entries live.
// Data-driven: HERMES_HOME/app-context.json overrides the bundled
// app-context.default.json so the block can be tuned without code edits.
// Invalid/missing files fall through to the bundled default; a broken block
// must never break prompt submission.
function parseAppContext(jsonText) {
  try {
    const parsed = JSON.parse(jsonText);
    if (!parsed || !Array.isArray(parsed.appLines)) return null;
    const lines = parsed.appLines.filter((l) => typeof l === 'string' && l.trim());
    return lines.length > 0 ? lines : null;
  } catch {
    return null;
  }
}

const APP_CONTEXT_CACHE_TTL_MS = 30 * 1000;
let appContextCache = { lines: null, readAt: 0 };

function appContextCandidatePaths() {
  const candidates = [];
  const hermesHome = process.env.HERMES_HOME;
  if (hermesHome) candidates.push(path.join(hermesHome, 'app-context.json'));
  candidates.push(path.join(__dirname, 'app-context.default.json'));
  return candidates;
}

function getAppContextLines(opts) {
  const candidates = (opts && Array.isArray(opts.candidates) && opts.candidates.length > 0)
    ? opts.candidates
    : appContextCandidatePaths();
  // 显式传 candidates 视为测试路径，绕过缓存；常规路径走 30s 缓存。
  const useCache = !(opts && opts.candidates);
  const now = Date.now();
  if (useCache && appContextCache.lines && now - appContextCache.readAt < APP_CONTEXT_CACHE_TTL_MS) {
    return appContextCache.lines;
  }
  let lines = null;
  for (const p of candidates) {
    try {
      if (!fs.existsSync(p)) continue;
      lines = parseAppContext(fs.readFileSync(p, 'utf8'));
      if (lines) break;
    } catch { /* try next candidate */ }
  }
  const result = lines || [];
  if (useCache) appContextCache = { lines: result, readAt: now };
  return result;
}

function prependEnvContext(ctx, rawText, extras) {
  if (!rawText) return rawText;
  if (/^\[环境上下文\]/.test(rawText)) return rawText;       // already injected
  const enabled = !(ctx && ctx.forwardedProps && ctx.forwardedProps.env_aware === false);
  if (!enabled) return rawText;
  // 可选扩展行：app 自我认知块（静态，数据驱动）在前，微信桥状态（动态）在后。
  // 由调用方按当前运行时状态生成，测试可省略。
  const extraParts = [];
  if (extras && Array.isArray(extras.appLines)) {
    for (const l of extras.appLines) {
      if (typeof l === 'string' && l.trim()) extraParts.push(l.trim());
    }
  }
  if (extras && typeof extras.wechatLine === 'string' && extras.wechatLine) {
    extraParts.push(extras.wechatLine);
  }
  const extraLines = extraParts.length > 0 ? `\n${extraParts.join('\n')}\n` : '\n';
  const env =
    `[环境上下文] 当前时间：${formatNowForModel()}\n` +
    `- 日期、时间、星期、电话号码、身份证号、版本号、引用的数字等必须是真实数据；` +
    `不知道就说不知道，不要凭印象编造。\n` +
    `- 一次性给出最终答案，不要"我理解错了 / 刚才那个回答确实…"式的自我反思重写。\n` +
    `- 运行环境：Windows 桌面（Electron 便携应用）。` +
    `执行命令时注意：\n` +
    `  · 没有全局 \`python3\`/python 命令（Windows 应用执行别名会报 "Python was not found"）。` +
    `执行 Python 用 \`python\`（Hermes 已把自带 venv 的 Scripts 目录加进 PATH），` +
    `或完整路径 \`hermes-fork\\.venv\\Scripts\\python.exe\`。\n` +
    `  · 终端工具的执行环境是 Windows cmd，不是 Git Bash/POSIX：` +
    `\`/tmp/xxx\`、heredoc（\`cat > x << EOF\`）、\`python3\` 这类写法不可用。` +
    `临时脚本写到项目目录下的绝对路径，再用 \`python\` 执行。` +
    `${extraLines}` +
    `---\n`;
  return `${env}\n${rawText}`;
}

// ── Stream delta handling ────────────────────────────────────────────────
// Agnes (custom provider) streams `content` as PURE INCREMENTAL deltas —
// words split word-wise, digits split char-wise ("11" → "1","1";
// "2026" → "2","0","2","6"). Verified live in scripts/agnes_delta_probe.py.
// Therefore agui-server must NOT dedup stream deltas: any dedup (substring
// or suffix matching) will swallow legitimate single-char digits and
// duplicate runs, garbling dates/numbers ("2026年08月26日" → "206年8月日").
// The only place a "repeat" can occur is message.complete re-delivering the
// full text — handled precisely by finalizeRemainder below (prefix/suffix
// based, no heuristics, no thresholds).

function appendStreamDelta(delta) {
  // Stream deltas are pure increments: forward them verbatim.
  if (!delta || typeof delta !== 'string') return '';
  return delta;
}

function finalizeRemainder(emittedText, text) {
  // Compute the part of the message.complete full text that still needs to
  // be sent. Precise prefix/suffix check, zero heuristics:
  //   - text is already the tail of emittedText  → ''   (fully streamed)
  //   - emittedText is a prefix of text          → text.slice(emittedText.length)
  //   - otherwise (drift / nothing streamed)     → text (send in full)
  if (!text || typeof text !== 'string') return '';
  if (emittedText && emittedText.endsWith(text)) return '';
  if (emittedText && text.startsWith(emittedText)) return text.slice(emittedText.length);
  return text;
}

module.exports = {
  createAgUIServer,
  extractText,
  formatNowForModel,
  prependEnvContext,
  buildWechatEnvLine,
  parseAppContext,
  getAppContextLines,
  appendStreamDelta,
  finalizeRemainder,
};
