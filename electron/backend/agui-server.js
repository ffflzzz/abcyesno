const express = require('express');
const cors = require('cors');
const net = require('net');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { EventEncoder } = require('@ag-ui/encoder');
const { v4: uuidv4 } = require('uuid');
const { log } = require('./logger');

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
// Video-task heuristic (used both for legacy auto-delegation and the @ parser).
function looksLikeVideoTask(t) {
  if (!t) return false;
  const lower = t.toLowerCase();
  const keywords = ['视频', '剪映', 'jianying', 'manju', '做一条', '生成视频', 'video', 'manjucraft', 'manju-craft'];
  return keywords.some((k) => lower.includes(k));
}

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

  // Agent 自渲染 UI 组件层：普通对话轮次（非 workflow）的活跃 SSE sender。
  // render_ui tool 在 Hermes 进程内通过 HTTP 桥把 ui.render 事件回传，这里按
  // runId 路由到当前对话轮的 SSE 流。与 workflowSubscribers 平行，但覆盖所有轮次。
  const activeTurnSenders = new Map();

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
            if (data && data.id) out.push(data);
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
  //   3. legacy auto-delegation of video tasks to manju_craft (main assistant)
  // Returns the matched manifest object, or null. The caller opens the HITL
  // subscriber keyed by this workflow so its workflow.* events relay back.
  // Defined INSIDE createAgUIServer so it closes over discoverManifests()
  // (which is function-local here, not module-scope).
  function resolveMentionDelegation(text, mentions, skillId) {
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
    // 3. legacy video auto-delegation (main assistant only)
    if ((!skillId || skillId === 'default') && looksLikeVideoTask(text)) {
      const manju = mfMap.get('manju_craft');
      if (manju && !targets.includes(manju)) targets.push(manju);
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
    return { method, agentId, threadId, runId, messages, forwardedProps, assistantId, skillId, requestedModel };
  }

  async function ensureHermesSession(client, ctx) {
    const existing = await storage.getThreadMapping(ctx.threadId);
    if (existing) {
      try {
        // Validate that the mapped Hermes session is still alive (it may have
        // been lost when Hermes restarted or was evicted from memory).
        await client.request('session.status', { session_id: existing }, 5000);
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
    // Map frontend assistant skill IDs to Hermes skill IDs.
    const skillId = ctx.skillId;
    if (skillId && skillId !== 'default') {
      // The actual Hermes skill is 'langgraph-agents'; 'manju-craft' is just
      // a frontend assistant label pointing to the manju_craft LangGraph agent.
      createParams.skill_id = skillId === 'manju-craft' ? 'langgraph-agents' : skillId;
    }
    log('agui-server', `session.create params: ${JSON.stringify(createParams)}`);
    const created = await client.request('session.create', createParams, 30000);
    const hermesSessionId = created?.session_id || created?.id;
    if (!hermesSessionId) {
      throw new Error('session.create did not return a session id');
    }
    await storage.setThreadMapping(ctx.threadId, hermesSessionId);
    return { id: hermesSessionId, created: true };
  }

  function sendSSE(res, encoder, event) {
    try {
      res.write(encoder.encode(event));
    } catch (err) {
      log('agui-server', `write error: ${err.message}`);
    }
  }

  function createTurnTranslator(res, encoder, ctx) {
    const messageId = `msg-${ctx.runId || uuidv4()}`;
    let messageStarted = false;
    let hasTextDelta = false;
    let hasRunError = false;
    let emittedText = '';
    let emittedPlain = ''; // whitespace-normalized, for overlap checks
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

    function ensureMessageStarted(role = 'assistant') {
      if (!messageStarted) {
        messageStarted = true;
        send({ type: 'TEXT_MESSAGE_START', messageId, role });
      }
    }

    // Compute the longest suffix of `base` that matches a prefix of `candidate`.
    // Returns the length of that overlap (0 if none). Tries longer overlaps first
    // so small coincidental substrings don't steal real content.
    function overlapLen(base, candidate) {
      const max = Math.min(base.length, candidate.length);
      for (let k = max; k > 0; k--) {
        if (base.slice(-k) === candidate.slice(0, k)) return k;
      }
      return 0;
    }

    function normalizeForDedup(text) {
      // Collapse whitespace but keep a single space so overlapping sentences still match.
      return text.replace(/\s+/g, ' ').trim();
    }

    // Append `delta` to the emitted text, skipping any part that is already present
    // at the end. This handles both true incremental deltas and accidental cumulative
    // or partially-repeated deltas from downstream providers.
    function appendDelta(delta) {
      if (!delta || typeof delta !== 'string') return '';
      // Fast path: the entire delta is already the suffix of what we sent.
      if (emittedText && emittedText.endsWith(delta)) return '';
      const overlap = overlapLen(emittedText, delta);
      let actual = delta;
      if (overlap > 0) {
        actual = delta.slice(overlap);
      }
      // Second line of defence: if the whitespace-normalized version is fully
      // contained, drop it. This catches cases where punctuation/spacing differ.
      const plainActual = normalizeForDedup(actual);
      const plainDelta = normalizeForDedup(delta);
      if (plainActual && emittedPlain.includes(plainActual)) {
        actual = '';
      } else if (!actual && plainDelta && emittedPlain.includes(plainDelta)) {
        actual = '';
      }
      return actual;
    }

    function recordEmitted(delta) {
      emittedText += delta;
      emittedPlain = normalizeForDedup(emittedText);
    }

    function emitTextDelta(delta) {
      const actual = appendDelta(delta);
      log('agui-server', `[translator] delta len=${delta.length} actualLen=${actual.length} emittedLen=${emittedText.length} text=${JSON.stringify(delta.slice(0, 120))}`);
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
      // Always run the complete text through the same incremental deduplication
      // so a cumulative `message.complete` cannot re-emit content that was
      // already streamed as deltas (including repeated thinking prefixes).
      if (text && typeof text === 'string') {
        const actual = appendDelta(text);
        if (actual) {
          ensureMessageStarted();
          hasTextDelta = true;
          recordEmitted(actual);
          send({ type: 'TEXT_MESSAGE_CONTENT', messageId, delta: actual });
        }
      }
      ensureMessageStarted();
      send({ type: 'TEXT_MESSAGE_END', messageId });
    }

    function handleEvent(type, params) {
      const payload = params && params.payload ? params.payload : params;
      const sid = params && (params.session_id || params.sid);
      if (sid && sid !== ctx.hermesSessionId) return;
      const textPreview = payload && typeof payload.text === 'string' ? payload.text.slice(0, 100) : '';
      log('agui-server', `[translator] event=${type} sid=${sid || ''} textPreview=${JSON.stringify(textPreview)}`);

      switch (type) {
        case 'message.start':
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

        case 'reasoning.available':
          // Structured reasoning (non-verbose) — forward for the indicator.
          if (payload && typeof payload.text === 'string' && payload.text.trim()) {
            setPhase('thinking');
            send({ type: 'CUSTOM', name: 'thinking.delta', value: { text: payload.text } });
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

        case 'message.complete':
        case 'message.end': {
          const text = payload && (payload.text || payload.rendered);
          setPhase('idle');
          finalize(text);
          break;
        }

        case 'status.update': {
          let text = payload && typeof payload.text === 'string' ? payload.text : '';
          const isError = payload && (payload.kind === 'error' || /❌|error|fail|timed out/i.test(text));
          if (isError) {
            hasRunError = true;
            send({ type: 'RUN_ERROR', runId: ctx.runId, message: text });
          } else if (text) {
            // Suppress verbose reasoning / kaomoji noise from status lines.
            // Patterns: ◎_◎ reasoning..., ( ˘ ˘)♡ cogitating, etc.
            if (/^[◎◯][_ ]?[◎◯]|[\(（][^\n]*[\)）]\s*[♡♥❤💖]?\s*[a-z]+ing\b|[a-z]+\.(reasoning|mulling|thinking|deliberating|pondering)/i.test(text)) {
              text = '';
            }
            if (text && !hasTextDelta) {
              ensureMessageStarted('assistant');
              emitTextDelta(text);
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

        default:
          // Forward anything else as a raw source event for debugging.
          send({ type: 'RAW', runId: ctx.runId, event: { type, params }, source: 'hermes' });
      }
    }

    return { handleEvent, finalize, hasError: () => hasRunError };
  }

  function waitForHermesTurn(client, hermesSessionId, ctx, res, encoder, timeoutMs = 120000) {
    const translator = createTurnTranslator(res, encoder, { ...ctx, hermesSessionId });

    const promise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup();
        translator.finalize('');
        reject(new Error('Hermes turn timed out'));
      }, timeoutMs);

      const cleanup = () => {
        clearTimeout(timer);
        client.off('event', handler);
      };

      const handler = (type, params) => {
        try {
          translator.handleEvent(type, params);
        } catch (err) {
          log('agui-server', `event translation error: ${err.message}`);
        }

        if (type === 'message.complete' || type === 'message.end') {
          cleanup();
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
    });

    return { promise, translator };
  }

  async function handleAgentConnect(req, res, encoder, ctx) {
    const client = gatewayClient();
    if (!client || !client.ready) {
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
    if (!client || !client.ready) {
      sendSSE(res, encoder, { type: 'RUN_ERROR', message: 'Hermes gateway not connected' });
      return res.end();
    }

    const userMsgs = (ctx.messages || []).filter((m) => m.role === 'user');
    const lastUser = userMsgs[userMsgs.length - 1];
    let text = lastUser && lastUser.content ? extractText(lastUser.content) : '';

    // @ mention protocol (spec §2): resolve which workflow this turn delegates
    // to. Either an explicit `mentions` array, an `@Name` token in the text, or
    // legacy video auto-delegation. Opens the HITL subscriber so the workflow's
    // workflow.* events relay back to this SSE stream.
    const mentionTarget = resolveMentionDelegation(text, ctx.forwardedProps.mentions, ctx.skillId);
    let delegatedAgent = ctx.skillId === 'manju-craft' ? 'manju_craft' : (mentionTarget ? mentionTarget.id : null);

    // A structured ContractForm / Workbench invoke ("请调用 langgraph_agent 工具…"
    // carrying agent_name) also targets a workflow. Resolve its agent_name so the
    // HITL/workflow subscriber opens (wfDelegated) and the Python tool can stream
    // workflow.* events back, instead of falling back to sync graph.invoke().
    const isStructuredInvoke =
      /langgraph_agent/.test(text) && /agent_name/.test(text);
    if (isStructuredInvoke) {
      const m = text.match(/"agent_name"\s*:\s*"([^"]+)"/);
      if (m) delegatedAgent = m[1];
    }

    const wfDelegated =
      !!delegatedAgent ||
      ((!ctx.skillId || ctx.skillId === 'default') && looksLikeVideoTask(text));
    let wfRunId = null;

    // Contract layer: if the frontend already sent a structured invocation
    // (from a ContractForm / Workbench), route it directly - no hardcoded
    // rewriting. The agent_name comes from the manifest, so this is
    // data-driven, not a per-workflow branch.
    if (!isStructuredInvoke) {
    if (delegatedAgent && text) {
      // A workflow (via @ mention, sidebar entry, or video auto-delegation) is
      // targeted: instruct the model to invoke that LangGraph agent directly.
      text = [
        '请立即调用 langgraph_agent 工具，参数如下：',
        '{',
        `  "agent_name": "${delegatedAgent}",`,
        `  "input": ${JSON.stringify(text)}`,
        '}',
        '不要解释、不要加载 skill、不要调用其它工具，直接发起 langgraph_agent 调用。',
      ].join('\n');
    } else if ((!ctx.skillId || ctx.skillId === 'default') && text && looksLikeVideoTask(text)) {
      // When talking to the main assistant, proactively delegate video-generation
      // tasks to the dedicated manju_craft agent. The langgraph_agent tool is part
      // of the hermes-cli toolset, so the model can invoke it and the frontend will
      // render the tool call card.
      text = [
        '用户请求如下：',
        `${text}`,
        '',
        '你是主助手。如果上述请求涉及视频生成、剪映或 manju-craft 工作流，',
        '请直接调用 langgraph_agent 工具，参数为：',
        '{',
        '  "agent_name": "manju_craft",',
        `  "input": ${JSON.stringify(text)}`,
        '}',
        '不要解释你打算做什么，直接发起 langgraph_agent 调用；工具执行结果会返回给用户。',
      ].join('\n');
    }
    }
    if (!text) {
      sendSSE(res, encoder, { type: 'RUN_STARTED', threadId: ctx.threadId, runId: ctx.runId });
      sendSSE(res, encoder, { type: 'RUN_FINISHED', threadId: ctx.threadId, runId: ctx.runId });
      return res.end();
    }

    // Open the HITL subscriber for the delegated workflow and publish the
    // workflowRunId so the Python tool can map its events back to this SSE stream.
    if (wfDelegated) {
      wfRunId = 'wf-' + ctx.runId;
      workflowSubscribers.set(wfRunId, (obj) => sendSSE(res, encoder, obj));
      try {
        const base = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
        const dir = path.join(base, 'workflow_hitl');
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(
          path.join(dir, '.wf_active.json'),
          JSON.stringify({ runId: wfRunId, threadId: ctx.threadId }),
          'utf-8'
        );
        log('agui-server', `registered HITL subscriber ${wfRunId}`);
      } catch (e) {
        log('agui-server', `HITL coord write failed: ${e.message}`);
      }
    }

    sendSSE(res, encoder, { type: 'RUN_STARTED', threadId: ctx.threadId, runId: ctx.runId });

    // Register this turn's SSE sender so render_ui (and any future agent-driven
    // UI) can push ui.render events back from the Hermes process over HTTP.
    const turnSend = (obj) => sendSSE(res, encoder, obj);
    activeTurnSenders.set(ctx.runId, turnSend);
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

    async function runOnce(hermesSessionId) {
      // The turn can stay open for a long time when a workflow pauses at a
      // human-in-the-loop approval gate (the graph waits on the control-file
      // channel until the user decides). Default to 30 min; override via env.
      const turnTimeoutMs = Number(process.env.ABC_AGUI_TURN_TIMEOUT || 1800000);
      const { promise: turnPromise, translator } = waitForHermesTurn(client, hermesSessionId, ctx, res, encoder, turnTimeoutMs);
      await client.request('prompt.submit', { session_id: hermesSessionId, text }, 120000);
      await turnPromise;
      return translator;
    }

    try {
      let hermesSessionId = await getReadyHermesSession();
      let translator;
      try {
        translator = await runOnce(hermesSessionId);
      } catch (firstErr) {
        const msg = firstErr && firstErr.message ? firstErr.message : String(firstErr);
        log('agui-server', `prompt.submit error: ${msg}`);
        // If the session disappeared (e.g. Hermes restarted), clear the stale
        // mapping and recreate the session once.
        if (msg.toLowerCase().includes('session not found')) {
          log('agui-server', `clearing stale thread mapping ${ctx.threadId} -> ${hermesSessionId}`);
          await storage.setThreadMapping(ctx.threadId, null);
          hermesSessionId = await getReadyHermesSession();
          translator = await runOnce(hermesSessionId);
        } else {
          throw firstErr;
        }
      }
      if (translator && !translator.hasError()) {
        sendSSE(res, encoder, { type: 'RUN_FINISHED', threadId: ctx.threadId, runId: ctx.runId });
      }
    } catch (err) {
      log('agui-server', `agent/run error: ${err.message}`);
      sendSSE(res, encoder, { type: 'RUN_ERROR', message: err.message });
    }
    if (wfDelegated && wfRunId) workflowSubscribers.delete(wfRunId);
    activeTurnSenders.delete(ctx.runId);
    clearUiActiveCoord();
    res.end();
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
        log('agui-server', `ui-event dropped (no active turn) for ${runId}`);
        return res.json({ status: 'dropped', runId });
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
      const eventType = body.type;
      const payload = body.payload || {};
      if (!runId || !eventType) {
        return res.json({ status: 'error', message: 'runId and type are required' });
      }
      const send = workflowSubscribers.get(runId);
      if (!send) {
        log('agui-server', `workflow-event dropped (no subscriber) for ${runId}`);
        return res.json({ status: 'dropped', runId });
      }
      send({ type: 'CUSTOM', name: `workflow.${eventType}`, value: payload });
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

module.exports = { createAgUIServer };
