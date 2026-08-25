import { useState, useRef, useCallback, useEffect } from "react";
import { emitContractEvent } from "../contract/eventBus.js";
import { emitToastShow, emitToastClear } from "./uiBus.js";

// ── Inline images → native vision ─────────────────────────────────────
// The composer embeds a pasted/dropped screenshot straight into the message
// text as `![图片1](data:image/png;base64,....)` so the user's own bubble can
// render it. Shipping that data URL to the backend *as text* is what killed
// vision: the model received tens of thousands of base64 characters instead
// of an image (and blew the context budget — "input length too long").
//
// So we split the two concerns:
//   • local message  → keeps the data URL, bubble still shows the picture
//   • wire payload   → data URL replaced by a short `[附图N]` marker, and the
//                      real bytes travel in a separate `images` array that
//                      agui-server hands to Hermes via `image.attach_bytes`,
//                      which is the same path the TUI uses for native vision.
const INLINE_IMG_RE =
  /!\[([^\]]*)\]\((data:image\/[a-zA-Z0-9.+-]+;base64,[^)\s]+|file:\/\/[^)\s]+)\)/g;

function splitInlineImages(text) {
  if (!text || typeof text !== "string") return { text: text || "", images: [] };
  const images = [];
  const stripped = text.replace(INLINE_IMG_RE, (_m, alt, url) => {
    images.push({ alt: alt || "", url });
    return `[附图${images.length}]`;
  });
  return { text: stripped, images };
}

// Cheap pre-check so we don't run the regex over every history entry.
function hasInlineImage(s) {
  return typeof s === "string" && (s.includes("](data:image/") || s.includes("](file://"));
}

function guessImageName(url, idx) {
  if (url.startsWith("data:")) {
    const m = /^data:image\/([a-zA-Z0-9.+-]+);/.exec(url);
    const ext = (m && m[1] ? m[1] : "png").replace("jpeg", "jpg");
    return `pasted_${idx + 1}.${ext}`;
  }
  try {
    const tail = decodeURIComponent(url.split("?")[0]).split(/[\\/]/).pop();
    return tail || `image_${idx + 1}.png`;
  } catch {
    return `image_${idx + 1}.png`;
  }
}

// Kawaii spinner phrases emitted by conversation_loop.py (e.g. "٩(๑❛ᴗ❛๑)۶ formulating...").
// These are transient CLI-style status updates and must NOT be accumulated in thinkingText.
const KAWAII_SPINNER_RE = new RegExp(
  "^\\s*(?:\\S*\\([^)]{1,30}\\)\\S*|[^\\p{L}\\p{N}\\s]{1,10})\\s+" +
    "(pondering|contemplating|musing|cogitating|ruminating|deliberating|mulling|" +
    "reflecting|processing|reasoning|analyzing|computing|synthesizing|formulating|brainstorming)" +
    "\\s*\\.\\.\\.\\s*$",
  "iu"
);
function isKawaiiSpinnerPhrase(text) {
  return typeof text === "string" && KAWAII_SPINNER_RE.test(text.trim());
}

// Extract a background terminal process id from a tool result. Hermes'
// terminal tool (pty=True, background=True) returns JSON like
// { output, session_id, pid, exit_code } — session_id is the
// process_registry id that agent.terminal.output events route by.
function extractProcessId(result) {
  if (!result) return null;
  let obj = result;
  if (typeof result === "string") {
    const trimmed = result.trim();
    if (!trimmed.startsWith("{")) return null;
    try {
      obj = JSON.parse(trimmed);
    } catch {
      return null;
    }
  }
  if (typeof obj !== "object" || obj === null) return null;
  const pid = obj.session_id || obj.process_id || obj.processId;
  return typeof pid === "string" && pid ? pid : null;
}

// Resolve every inline reference to base64 bytes. `file://` needs a trip
// through the main process (the renderer can't read cross-directory file://).
async function resolveImagePayload(refs) {
  const out = [];
  for (let i = 0; i < refs.length; i++) {
    const { alt, url } = refs[i];
    const filename = guessImageName(url, i);
    if (url.startsWith("data:")) {
      out.push({ alt, dataUrl: url, filename });
      continue;
    }
    if (typeof window === "undefined" || !window.hermes?.readLocalImage) continue;
    try {
      const r = await window.hermes.readLocalImage(url);
      if (r && r.dataUrl) out.push({ alt, dataUrl: r.dataUrl, filename });
    } catch {
      /* unreadable file: drop it rather than fail the whole send */
    }
  }
  return out;
}

// ── Agent 自渲染 UI 组件：白名单 + blockId 校验 ──
const UI_BLOCK_TYPES = new Set(["table", "flowchart", "card", "progress", "action"]);
const BLOCK_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/;
// 渲染上限，防恶意大 payload 卡死渲染（spec §6.4）
const UI_BLOCK_MAX = 64;

// Stall detection
const STALL_CHECK_INTERVAL = 5000;
const STALL_THRESHOLD_MS = 60000;
// SSE reader idle timeout. Long research runs can go minutes between visible
// events (model reasoning, slow tool calls), so 2 minutes falsely aborts them.
// Align with the agui-server turn timeout (default 30 min) minus a safety margin.
const READ_TIMEOUT_MS = 1_800_000;

const EMPTY_SNAPSHOT = Object.freeze({
  messages: [],
  phase: "idle",
  thinkingText: "",
  error: null,
  uiBlocks: [],
  stalled: false,
});

/**
 * 单个会话的运行时状态。**可变对象**——事件处理直接原地改字段，
 * 但集合类字段（messages / uiBlocks）始终用新数组替换，保证 React 能
 * 通过引用比较感知变化。
 */
function createSessionState(id) {
  return {
    id,
    // 可见状态（会被投影进 snapshot）
    messages: [],
    phase: "idle",
    thinkingText: "",
    error: null,
    uiBlocks: [],
    stalled: false,
    // ── P1 新增可见状态（深度推理 / 状态行 / 工具进度 / 子 agent / MOA / 用量 / 评审）──
    reasoningText: "",
    statusLine: "",
    statusKind: "",
    toolStatus: {},            // { [toolName]: { generating, preview } }
    subagents: [],             // 子 agent 实时镜像
    moaRefs: [],               // MOA 多模型参考
    moaAggregating: null,      // 当前聚合器名（moa.aggregating）
    usage: null,               // 后端真实 token/cost（message.complete.usage / session.usage）
    reviewSummary: null,       // 评审摘要（review.summary）
    browserProgress: [],       // 浏览器自动化进度（browser.progress）：[{message,level,ts}]
    // 运行时内部状态
    controller: null,
    runId: null,
    currentAssistantId: null,
    toolIndex: new Map(),
    // process_id → tool message id：agent.terminal.output 按 process_id 路由到
    // 对应的 terminal 工具卡片（后台 PTY 会话的实时 ANSI 输出）。
    processIndex: new Map(),
    thinkingSince: null,
    hydrated: false,
  };
}

function snapshotOf(s) {
  return {
    messages: s.messages,
    phase: s.phase,
    thinkingText: s.thinkingText,
    error: s.error,
    uiBlocks: s.uiBlocks,
    stalled: s.stalled,
    reasoningText: s.reasoningText,
    statusLine: s.statusLine,
    statusKind: s.statusKind,
    toolStatus: s.toolStatus,
    subagents: s.subagents,
    moaRefs: s.moaRefs,
    moaAggregating: s.moaAggregating,
    usage: s.usage,
    reviewSummary: s.reviewSummary,
    browserProgress: s.browserProgress,
  };
}

function sameIds(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

/** Shallow equality for plain objects — used to deduplicate ui.render events. */
function shallowEqual(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  const keysA = Object.keys(a);
  if (keysA.length !== Object.keys(b).length) return false;
  for (const k of keysA) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

/**
 * useAgentStream — 多会话并发 SSE token 流状态机
 *
 * 直接消费 agui-server 的 /api/ag-ui/run SSE 端点，完全绕开 CopilotKit。
 * 事件格式（@ag-ui/encoder）：`data: {JSON.stringify(event)}\n\n`
 *
 * ## 并发模型（2026-08-01 重构）
 * Hermes 后端天生支持多会话独立并发：`ensureHermesSession` 为每个 threadId
 * 映射一个独立的 Hermes session_id。此前前端只有一个 abortRef，导致同一时刻
 * 只能存活一条 SSE 流——切换会话即掐断上一条，后台 turn 仍在烧 token 但增量
 * 无人接收、也不会入库，结果永久丢失。
 *
 * 现在每个 sessionId 拥有独立的 SessionState（controller / messages / phase /
 * uiBlocks / toolIndex）。切换会话只切换"投影"，不影响任何在跑的流。
 *
 * ## 渲染策略
 * - 前台（activeSessionId）会话：变更打标 dirty，rAF 合帧后投影为 snapshot state。
 * - 后台会话：只写 Map，**不触发任何 React 渲染**，零开销累积。
 * - 会话运行/结束（phase idle 边界）时更新 runningSessionIds（低频）。
 *
 * @param aguiPort         agui-server 端口
 * @param activeSessionId  当前前台会话 id
 * @param options.onSettled (sessionId, messages) => void，任一会话 run 结束时回调，
 *                          用于持久化（后台会话完成同样触发）
 */
export function useAgentStream(aguiPort, activeSessionId, options = {}) {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);
  const [runningSessionIds, setRunningSessionIds] = useState([]);

  const sessionsRef = useRef(new Map());
  const activeIdRef = useRef(activeSessionId || "");
  const dirtyRef = useRef(new Set());
  const rafScheduledRef = useRef(false);
  const runningIdsRef = useRef([]);

  // onSettled 存 ref，避免回调身份变化导致事件处理器重建
  const onSettledRef = useRef(options.onSettled);
  onSettledRef.current = options.onSettled;

  // ── 会话表访问 ──────────────────────────────────────────────
  const getSession = useCallback((id) => {
    const key = id || "";
    let s = sessionsRef.current.get(key);
    if (!s) {
      s = createSessionState(key);
      sessionsRef.current.set(key, s);
    }
    return s;
  }, []);

  const syncRunning = useCallback(() => {
    const ids = [];
    for (const [id, s] of sessionsRef.current) {
      if (s.phase !== "idle" && id) ids.push(id);
    }
    if (sameIds(ids, runningIdsRef.current)) return;
    runningIdsRef.current = ids;
    setRunningSessionIds(ids);
  }, []);

  const flush = useCallback(() => {
    rafScheduledRef.current = false;
    const dirty = dirtyRef.current;
    if (dirty.size === 0) return;
    dirtyRef.current = new Set();
    const active = activeIdRef.current;
    if (dirty.has(active)) {
      const s = sessionsRef.current.get(active);
      setSnapshot(s ? snapshotOf(s) : EMPTY_SNAPSHOT);
    }
    syncRunning();
  }, [syncRunning]);

  /** 标记某会话有可见变更；仅当它是前台会话时才会触发渲染。 */
  const publish = useCallback(
    (id) => {
      dirtyRef.current.add(id || "");
      if (rafScheduledRef.current) return;
      rafScheduledRef.current = true;
      requestAnimationFrame(flush);
    },
    [flush]
  );

  /** 立即投影（跳过 rAF），用于会话切换等需要同步生效的场景。 */
  const publishNow = useCallback(
    (id) => {
      const key = id || "";
      dirtyRef.current.delete(key);
      if (key === activeIdRef.current) {
        const s = sessionsRef.current.get(key);
        setSnapshot(s ? snapshotOf(s) : EMPTY_SNAPSHOT);
      }
      syncRunning();
    },
    [syncRunning]
  );

  // 切换前台会话：在 render 期直接调整投影，而不是等 effect。
  // 走 effect 会先用旧会话的 messages 渲染一帧，造成切换时的内容闪烁。
  // 这是 React 官方的 "adjusting state when a prop changes" 模式。
  const [projectedId, setProjectedId] = useState(activeSessionId || "");
  if ((activeSessionId || "") !== projectedId) {
    const key = activeSessionId || "";
    activeIdRef.current = key;
    setProjectedId(key);
    const s = sessionsRef.current.get(key);
    setSnapshot(s ? snapshotOf(s) : EMPTY_SNAPSHOT);
  }

  // ── 消息操作 helpers（作用于指定会话对象）──────────────────
  const appendMessage = useCallback(
    (sess, msg) => {
      sess.messages = [...sess.messages, msg];
      publish(sess.id);
    },
    [publish]
  );

  const patchMessage = useCallback(
    (sess, id, patch) => {
      sess.messages = sess.messages.map((m) => (m.id === id ? { ...m, ...patch } : m));
      publish(sess.id);
    },
    [publish]
  );

  const appendToMessage = useCallback(
    (sess, id, delta) => {
      sess.messages = sess.messages.map((m) =>
        m.id === id ? { ...m, content: (m.content || "") + delta } : m
      );
      publish(sess.id);
    },
    [publish]
  );

  /** run 结束（正常/出错/中断后自然收尾）：落库回调 */
  const settle = useCallback((sess) => {
    const cb = onSettledRef.current;
    if (!cb || !sess.id) return;
    try {
      cb(sess.id, sess.messages);
    } catch (err) {
      console.error("onSettled failed", err);
    }
  }, []);

  /**
   * 把本会话所有仍在运行的 tool message 强制标为 interrupted，
   * 并清掉 toolStatus 里的 generating 映射。
   *
   * 背景：用户点“停止”或 SSE 被对端关时，TOOL_CALL_END 事件未必有机会发出，
   * 会留下 status=='running' 的工具卡一直“执行中…”。这里兜底收尾。
   *
   * 注意：只清“会让 UI 误显示运行中”的状态：
   * - toolStatus: {}（ToolCard 上的“生成中”徽标）
   * - messages 里的 tool 状态 -> interrupted
   * 不清 reasoningText / statusLine / subagents / moaRefs —— 这些是历史信息，
   * 用户重新查看消息时仍应可见（且已被 `loading && reasoningText` 等条件
   * 控制在流式期间才渲染，不会误显示运行中）。
   */
  const resetRunningTools = useCallback(
    (sess) => {
      let messagesChanged = false;
      const next = sess.messages.map((m) => {
        if (m && m.role === "tool" && (m.status === "running" || m.status === "in_progress")) {
          messagesChanged = true;
          // 没有 startedAt 也能算一个最小耗时，保证 ToolCard 能渲染
          const durationMs = m.startedAt ? Date.now() - m.startedAt : undefined;
          return { ...m, status: "interrupted", durationMs };
        }
        return m;
      });
      if (messagesChanged) sess.messages = next;

      const toolStatusChanged =
        sess.toolStatus && Object.keys(sess.toolStatus).length > 0;
      if (toolStatusChanged) sess.toolStatus = {};

      if (messagesChanged || toolStatusChanged) publish(sess.id);
    },
    [publish]
  );

  // ── CUSTOM 事件 ─────────────────────────────────────────────
  const handleCustom = useCallback(
    (sess, ev) => {
      const { name, value } = ev;
      if (name === "stream.phase") {
        const p = value?.phase;
        if (p) {
          sess.phase = p;
          publish(sess.id);
        }
      } else if (name === "thinking.delta") {
        const text = value?.text || "";
        const trimmed = text.trim();
        if (!trimmed) {
          // Backend clears the spinner between phases.
          sess.thinkingText = "";
          publish(sess.id);
          return;
        }
        // Kawaii spinner phrases from conversation_loop (e.g. "٩(๑❛ᴗ❛๑)۶ formulating...")
        // are transient CLI-style status updates. Show only the latest one instead of
        // accumulating them all, which matches the CLI spinner behavior.
        if (isKawaiiSpinnerPhrase(trimmed)) {
          sess.phase = "thinking";
          sess.thinkingText = trimmed;
          publish(sess.id);
          return;
        }
        // Real (non-spinner) thinking text: append, but replace any previous spinner
        // placeholder so they don't get mixed into the transcript.
        if (sess.thinkingText && isKawaiiSpinnerPhrase(sess.thinkingText)) {
          sess.thinkingText = "";
        }
        sess.phase = "thinking";
        sess.thinkingText += text;
        publish(sess.id);
      } else if (name === "reasoning.delta") {
        // Deep model reasoning tokens — rendered in a dedicated ReasoningBlock,
        // distinct from the shallow thinking indicator.
        const text = value?.text || "";
        if (!text) return;
        // Dedup: skip if the new delta is already a suffix of what we have.
        // Some upstream paths replay the last few tokens (reconnect / duplicate
        // emit) and without this we'd render the same trailing text twice.
        if (sess.reasoningText.endsWith(text)) return;
        sess.phase = "thinking";
        sess.reasoningText += text;
        publish(sess.id);
      } else if (name === "reasoning.snapshot") {
        // Complete reasoning snapshot from the model_progress callback.
        // Only use it as a FALLBACK when we haven't already streamed real
        // reasoning.delta this turn — otherwise the gateway's _think_text
        // scratchpad (often the answer body, not genuine thinking) would
        // overwrite the real reasoning and get hidden downstream as a duplicate.
        // See #thinking-visible.
        const text = value?.text || "";
        if (!text) return;
        if (sess.reasoningText && sess.reasoningText.trim()) return;
        sess.phase = "thinking";
        sess.reasoningText = text;
        publish(sess.id);
      } else if (name === "status.update") {
        sess.statusLine = value?.text || "";
        sess.statusKind = value?.kind || "";
        publish(sess.id);
      } else if (name === "tool.generating") {
        const tname = value?.name;
        if (tname) {
          sess.toolStatus = { ...sess.toolStatus, [tname]: { ...sess.toolStatus[tname], generating: true } };
          sess.statusLine = `⏳ 正在生成工具调用：${tname}`;
          publish(sess.id);
        }
      } else if (name === "tool.progress") {
        const tname = value?.name;
        const preview = value?.preview;
        if (tname) {
          sess.toolStatus = { ...sess.toolStatus, [tname]: { generating: true, preview: preview || (sess.toolStatus[tname] && sess.toolStatus[tname].preview) || null } };
          if (preview && typeof preview === "string" && preview.length <= 160) {
            sess.statusLine = `🔧 ${tname}：${preview}`;
          }
          publish(sess.id);
        }
      } else if (name === "tool.inline_diff") {
        const { toolCallId, diff } = value || {};
        const id = toolCallId && sess.toolIndex.get(toolCallId);
        if (id) patchMessage(sess, id, { inlineDiff: diff });
        // 仍触发一次 publish 让 UI 更新（patchMessage 内部已 publish，但保险）
        publish(sess.id);
      } else if (name === "notification.show") {
        emitToastShow({
          key: value?.key || value?.id,
          level: value?.level || "info",
          text: value?.text || "",
          ttlMs: value?.ttl_ms,
          kind: value?.kind,
        });
      } else if (name === "notification.clear") {
        emitToastClear({ key: value?.key });
      } else if (name && name.startsWith("subagent.")) {
        const p = value || {};
        const key = p.subagent_id || `task-${p.task_index}`;
        const prev = sess.subagents.find((s) => s.key === key);
        if (prev) {
          Object.assign(prev, p);
          prev.event = name;
        } else {
          sess.subagents = [...sess.subagents, { ...p, key, event: name }];
        }
        publish(sess.id);
      } else if (name === "moa.reference") {
        sess.moaRefs = [...sess.moaRefs, { label: value?.label, text: value?.text, index: value?.index }];
        publish(sess.id);
      } else if (name === "moa.aggregating") {
        sess.moaAggregating = value?.aggregator || true;
        sess.statusLine = value?.aggregator ? `🔀 MOA 聚合中（${value.aggregator}）` : "🔀 MOA 聚合中…";
        publish(sess.id);
      } else if (name === "background.complete") {
        emitToastShow({ key: value?.task_id, level: "success", text: `后台任务完成：${value?.text || value?.task_id || ""}`, kind: "ttl", ttlMs: 6000 });
      } else if (name === "review.summary") {
        sess.reviewSummary = value?.text || "";
        emitToastShow({ key: "review-summary", level: "info", text: "收到评审摘要", kind: "ttl", ttlMs: 4000 });
        publish(sess.id);
      } else if (name === "browser.progress") {
        // Agent-driven browser activity (route B / pw_browser_* tools). The
        // BrowserPanel mirrors these as a live progress log so the user watches
        // the agent operate the in-app Chromium. payload = { message, level }.
        const msg = value?.message;
        if (!msg) return;
        const entry = { message: msg, level: value?.level || "info", ts: Date.now() };
        // Cap to the last 60 entries to bound memory across a long run.
        sess.browserProgress = [...sess.browserProgress, entry].slice(-60);
        publish(sess.id);
      } else if (name === "usage.update") {
        sess.usage = value || null;
        publish(sess.id);
      } else if (name === "tool.chunk") {
        const { toolCallId, chunk } = value || {};
        const id = sess.toolIndex.get(toolCallId);
        if (id && chunk) {
          sess.messages = sess.messages.map((m) =>
            m.id === id ? { ...m, chunks: [...(m.chunks || []), chunk] } : m
          );
          publish(sess.id);
        }
      } else if (name === "agent.terminal.output") {
        // Live PTY output for a background terminal session (raw ANSI chunks,
        // streamed by Hermes' process_registry.on_output → tui_gateway). Route
        // by process_id to the owning tool message; rendered by TerminalToolCard.
        const { process_id: pid, chunk } = value || {};
        const id = pid && sess.processIndex.get(pid);
        if (id && chunk) {
          sess.messages = sess.messages.map((m) =>
            m.id === id
              ? { ...m, terminalChunks: [...(m.terminalChunks || []), chunk] }
              : m
          );
          publish(sess.id);
        }
      } else if (name === "terminal.close") {
        // PTY session was reaped server-side (request_close_terminal). Mark the
        // owning terminal card as ended so the pane goes read-only.
        const pid = (value && (value.process_id || value.processId)) || "";
        const id = pid && sess.processIndex.get(pid);
        if (id) {
          sess.messages = sess.messages.map((m) =>
            m.id === id ? { ...m, terminalClosed: true } : m
          );
          publish(sess.id);
        }
      } else if (name && name.startsWith("workflow.")) {
        // Contract layer (L5): relay progress / artifact / approval / done events
        // into the eventBus keyed by the run's threadId. The generic workbenches
        // subscribe via useContractEvents(session.id).
        emitContractEvent(sess.id, { type: name, payload: value });

        // 镜像进 sess.subagents：langgraph_agent 发的是 workflow.* 而非
        // subagent.*，所以对话里的 SubagentPanel（子智能体实时镜像）默认看不到
        // 它。这里把后台 workflow 的启动/进度/完成映射成一条 subagents 记录，
        // 并额外累积 topology + trace，让 SubagentPanel 展开后能渲染节点级 loop
        // 动画（复用 WorkflowGraphPanel），让用户不离开对话即可观察工作过程。
        const _wfKey = "__langgraph__";
        const _upsertWf = (patch) => {
          const _i = sess.subagents.findIndex((s) => s.key === _wfKey);
          if (_i >= 0) {
            sess.subagents = sess.subagents.map((s, idx) => (idx === _i ? { ...s, ...patch } : s));
          } else {
            sess.subagents = [...sess.subagents, { key: _wfKey, ...patch }];
          }
          publish(sess.id);
        };
        if (name === "workflow.started") {
          _upsertWf({
            goal: value?.agent || value?.workflowId || "后台工作流",
            status: "start",
            tool_name: value?.agent,
            event: "subagent.start",
          });
        } else if (name === "workflow.graph") {
          // 拓扑 + 总集数，供 WorkflowGraphPanel 渲染节点 DAG。
          _upsertWf({
            goal: value?.agent || value?.workflowId || "后台工作流",
            status: "start",
            tool_name: value?.agent,
            topology: { nodes: value?.nodes || [], edges: value?.edges || [] },
            total: value?.totalEpisodes || 1,
            event: "subagent.start",
          });
        } else if (name === "workflow.trace") {
          // 累积 node -> status 映射（running/done/pending/error），供
          // WorkflowGraphPanel 高亮当前节点与 loop 边。
          const _node = value?.node;
          if (_node) {
            const _prev = sess.subagents.find((s) => s.key === _wfKey);
            const _trace = { ...((_prev && _prev.trace) || {}), [_node]: value?.status };
            _upsertWf({
              status: "thinking",
              tool_name: value?.stage || value?.step_id || _node,
              trace: _trace,
              ...(typeof value?.episode === "number" ? { episode: value.episode } : {}),
              event: "subagent.thinking",
            });
          }
        } else if (name === "workflow.progress") {
          const _tn = value?.stage || value?.step_id;
          _upsertWf({
            status: "thinking",
            ...(_tn ? { tool_name: _tn } : {}),
            ...(value?.message ? { goal: value.message } : {}),
            event: "subagent.thinking",
          });
        } else if (name === "workflow.done") {
          _upsertWf({
            status: (!value?.status || value.status === "done") ? "complete" : value.status,
            event: "subagent.complete",
          });
        } else if (name === "workflow.error") {
          _upsertWf({
            status: "error",
            ...(value?.message ? { goal: value.message } : {}),
            event: "subagent.error",
          });
        }

        // B 方案：后台长任务已开始/结束，切换 reader 静默超时放宽开关。
        if (name === "workflow.started") {
          sess.backgroundRun = true;
        } else if (name === "workflow.done") {
          sess.backgroundRun = false;
        }

        // Surface workflow errors in the chat stream so they are not swallowed
        // when the workbench is not visible (e.g. chat-mode langgraph_agent).
        if (name === "workflow.error") {
          const msg = value?.message || "工作流运行出错";
          sess.error = msg;
          sess.phase = "idle";
          sess.thinkingSince = null;
          appendMessage(sess, {
            id: `wf-error-${Date.now()}`,
            role: "assistant",
            content: `❌ ${msg}`,
            createdAt: Date.now(),
            isError: true,
          });
          settle(sess);
        } else if (name === "workflow.done" && value?.status && value.status !== "done") {
          const status = value.status;
          const msg = value?.error || (status === "rejected" ? "工作流已被拒绝" : status === "timeout" ? "审批等待超时" : `工作流结束：${status}`);
          sess.error = msg;
          sess.phase = "idle";
          sess.thinkingSince = null;
          appendMessage(sess, {
            id: `wf-done-${Date.now()}`,
            role: "assistant",
            content: `⚠️ ${msg}`,
            createdAt: Date.now(),
            isError: true,
          });
          settle(sess);
        }
      } else if (name === "ui.render") {
        // Agent 自渲染 UI 组件能力（spec: AGENT_UI_RENDER_SPEC.md §3.1）。
        // 安全：未知 type 或非法 blockId 静默丢弃（§6）。
        const { blockId, type, props, replace, appendPreview } = value || {};
        if (!type || !UI_BLOCK_TYPES.has(type)) return;
        if (!blockId || !BLOCK_ID_RE.test(blockId)) return;
        const safeProps = props && typeof props === "object" ? props : {};
        const prev = sess.uiBlocks;
        const idx = prev.findIndex((b) => b.blockId === blockId);
        if (idx >= 0) {
          const old = prev[idx];
          // ── 去重：props 浅比较一致则跳过，避免无意义重渲染导致频闪 ──
          if (
            old.type === type &&
            shallowEqual(old.props, safeProps) &&
            !appendPreview
          ) {
            return; // 内容没变，不 publish
          }
          // 幂等更新：相同 blockId 直接替换（action 流式进度由 agent 发累积 props）。
          // 若声明 appendPreview 且旧块已有 preview，则仅追加 preview 文本。
          const next = prev.slice();
          if (
            appendPreview &&
            typeof next[idx].props?.preview === "string" &&
            typeof safeProps.preview === "string"
          ) {
            const merged = next[idx].props.preview + safeProps.preview;
            // 合并后如果 preview 也没变，同样跳过
            if (next[idx].props.preview === merged) return;
            next[idx] = {
              ...block,
              props: { ...safeProps, preview: merged },
            };
          } else {
            next[idx] = block;
          }
          sess.uiBlocks = next;
        } else {
          if (prev.length >= UI_BLOCK_MAX) return; // 硬上限，防恶意大 payload
          sess.uiBlocks = [...prev, block];
        }
        publish(sess.id);
      }
    },
    [publish, patchMessage]
  );

  // ── SSE 事件处理 ────────────────────────────────────────────
  const handleEvent = useCallback(
    (sess, ev) => {
      if (!ev || !ev.type) return;
      // Any incoming event means the stream is alive — clear stall flag
      if (sess.stalled) sess.stalled = false;
      const now = Date.now();

      switch (ev.type) {
        case "RUN_STARTED":
          sess.runId = ev.runId;
          sess.phase = "thinking";
          sess.thinkingText = "";
          sess.error = null;
          // 复位上一轮残留的 P1 状态
          sess.reasoningText = "";
          sess.statusLine = "";
          sess.statusKind = "";
          sess.toolStatus = {};
          sess.subagents = [];
          sess.moaRefs = [];
          sess.moaAggregating = null;
          sess.reviewSummary = null;
          sess.usage = null;
          sess.browserProgress = [];
          publish(sess.id);
          break;

        case "TEXT_MESSAGE_START":
          if (ev.role === "assistant") {
            sess.currentAssistantId = ev.messageId;
            appendMessage(sess, {
              id: ev.messageId,
              role: "assistant",
              content: "",
              createdAt: now,
              streaming: true,
            });
          }
          break;

        case "TEXT_MESSAGE_CONTENT":
          if (ev.messageId && ev.delta) {
            // Suppress Hermes interrupt metadata that leaks into user-visible
            // content (e.g. "Operation interrupted: waiting for model response").
            if (/^Operation interrupted:/.test(ev.delta)) break;
            if (sess.phase !== "text_generating") sess.phase = "text_generating";
            appendToMessage(sess, ev.messageId, ev.delta);
          }
          break;

        case "TEXT_MESSAGE_END":
          if (ev.messageId) {
            // 把本轮累积的深度推理文本绑定到这条 assistant 消息上，
            // 这样历史消息再次渲染时显示的是它自己的 reasoning，而不是当前全局的。
            patchMessage(sess, ev.messageId, {
              streaming: false,
              reasoning: sess.reasoningText || "",
            });
          }
          sess.currentAssistantId = null;
          break;

        case "TOOL_CALL_START": {
          sess.phase = "tool_executing";
          const toolMsg = {
            id: ev.toolCallId,
            role: "tool",
            toolName: ev.toolCallName || "tool",
            content: "",
            args: "",
            result: undefined,
            status: "running",
            createdAt: now,
            startedAt: now,
          };
          sess.toolIndex.set(ev.toolCallId, toolMsg.id);
          appendMessage(sess, toolMsg);
          break;
        }

        case "TOOL_CALL_ARGS": {
          const id = sess.toolIndex.get(ev.toolCallId);
          if (id && ev.delta) {
            sess.messages = sess.messages.map((m) =>
              m.id === id ? { ...m, args: (m.args || "") + ev.delta } : m
            );
            publish(sess.id);
          }
          break;
        }

        case "TOOL_CALL_RESULT": {
          const id = sess.toolIndex.get(ev.toolCallId);
          if (id) {
            patchMessage(sess, id, { result: ev.content, content: ev.content });
            // Background terminal (pty=True) returns a session_id in its tool
            // result. Index it so agent.terminal.output chunks can be routed to
            // this message's terminal pane.
            const pid = extractProcessId(ev.content);
            if (pid) {
              sess.messages = sess.messages.map((m) =>
                m.id === id ? { ...m, processId: pid } : m
              );
              sess.processIndex.set(pid, id);
            }
          }
          break;
        }

        case "TOOL_CALL_END": {
          const id = sess.toolIndex.get(ev.toolCallId);
          if (id) {
            patchMessage(sess, id, {
              status: ev.failed ? "error" : "complete",
              durationMs: ev.durationMs,
            });
          }
          sess.phase = "text_generating";
          publish(sess.id);
          break;
        }

        case "CUSTOM":
          handleCustom(sess, ev);
          break;

        case "RUN_ERROR":
          sess.error = ev.message || "运行出错";
          sess.phase = "idle";
          sess.thinkingSince = null;
          appendMessage(sess, {
            id: `error-${now}`,
            role: "assistant",
            content: ev.message || "运行出错",
            createdAt: now,
            isError: true,
          });
          settle(sess);
          break;

        case "RUN_FINISHED":
          sess.phase = "idle";
          sess.thinkingText = "";
          sess.thinkingSince = null;
          publish(sess.id);
          settle(sess);
          break;

        default:
          break;
      }
    },
    [publish, appendMessage, patchMessage, appendToMessage, handleCustom, settle]
  );

  // ── 发送消息（建立 SSE 连接）────────────────────────────────
  const sendMessage = useCallback(
    async (text, { threadId, assistantId, skillId, model, history, mentions, workspaceDir } = {}) => {
      const sess = getSession(threadId);
      if (!aguiPort) {
        sess.error = "aguiPort 未就绪";
        publish(sess.id);
        return;
      }
      // 同一会话内串行：新一轮开始前中止本会话上一条未结束的流。
      // **不影响其它会话**——这正是多会话并发的关键。
      if (sess.controller) {
        sess.controller.abort();
        sess.controller = null;
      }

      const runId = `run-${Date.now()}`;
      const userMsg = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        createdAt: Date.now(),
      };

      // 历史优先取本会话自己的消息（调用方可显式覆盖）。
      // 历史里的旧图同样要剥离 base64——否则每一轮都把整张图当文本重发一遍。
      const priorHistory = (
        history ||
        sess.messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({ id: m.id, role: m.role, content: m.content }))
      ).map((m) =>
        hasInlineImage(m.content) ? { ...m, content: splitInlineImages(m.content).text } : m
      );

      sess.messages = [...sess.messages, userMsg];
      sess.phase = "thinking";
      sess.thinkingText = "";
      sess.error = null;
      sess.stalled = false;
      sess.thinkingSince = Date.now();
      sess.uiBlocks = []; // 新的一轮：清空上一轮的 agent 自渲染组件
      // 复位 P1 状态
      sess.reasoningText = "";
      sess.statusLine = "";
      sess.statusKind = "";
      sess.toolStatus = {};
      sess.subagents = [];
      sess.moaRefs = [];
      sess.moaAggregating = null;
      sess.reviewSummary = null;
      sess.usage = null;
      sess.browserProgress = [];
      publishNow(sess.id);

      const controller = new AbortController();
      sess.controller = controller;

      // ── Vision: pull the images out of the text, ship them as real images ──
      const { text: wireText, images: imageRefs } = splitInlineImages(text);
      const attachedImages = imageRefs.length > 0 ? await resolveImagePayload(imageRefs) : [];

      const outgoing = [...priorHistory, { id: userMsg.id, role: "user", content: wireText }];

      try {
        const res = await fetch(`http://127.0.0.1:${aguiPort}/api/ag-ui/run`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            method: "agent/run",
            threadId: sess.id || `thread-${Date.now()}`,
            runId,
            messages: outgoing,
            forwardedProps: { assistantId, skillId, model, mentions: mentions || [], workspaceDir: workspaceDir || undefined },
            images: attachedImages.length > 0 ? attachedImages : undefined,
          }),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const readPromise = reader.read();
          // Safety timeout: if Chromium throttles the reader (background tab,
          // GIL pressure), abort instead of hanging forever so the user can retry.
          // 后台长任务（B 方案）期间不因节点静默 abort；agui-server 的
          // 60 分钟超时负责兜底关闭 SSE。
          const timer = setTimeout(() => {
            if (!sess.backgroundRun) controller.abort();
          }, READ_TIMEOUT_MS);
          let done;
          let value;
          try {
            ({ done, value } = await readPromise);
          } finally {
            clearTimeout(timer);
          }
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE 帧以 \n\n 分隔
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";

          for (const frame of frames) {
            const line = frame.trim();
            if (!line.startsWith("data:")) continue;
            const json = line.slice(5).trim();
            if (!json) continue;
            try {
              handleEvent(sess, JSON.parse(json));
            } catch {
              // 忽略单帧解析错误
            }
          }
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          sess.error = err.message || String(err);
          sess.phase = "idle";
          sess.thinkingSince = null;
          publish(sess.id);
          settle(sess);
        }
      } finally {
        if (sess.controller === controller) {
          sess.controller = null;
          // 流自然结束但后端没发 RUN_FINISHED（例如连接被对端关闭）时兜底收尾
          if (sess.phase !== "idle") {
            sess.phase = "idle";
            sess.thinkingText = "";
            sess.thinkingSince = null;
            sess.backgroundRun = false;
            publish(sess.id);
          }
          // SSE 被关 / 网络断：把仍处于 running 的工具卡标 interrupted（idempotent）
          resetRunningTools(sess);
          settle(sess);
        }
        syncRunning();
      }
    },
    [aguiPort, getSession, publish, publishNow, handleEvent, settle, syncRunning, resetRunningTools]
  );

  /**
   * 停止指定会话的前端流。
   * @param sessionId 省略则作用于当前前台会话
   * @param options.interruptBackend 仅语义标记；真正的后端中断由调用方通过
   *        window.hermes.interruptSession 发起（本 hook 不再自动 interrupt）。
   */
  const stop = useCallback(
    (sessionIdOrOptions) => {
      let sid = activeIdRef.current;
      if (typeof sessionIdOrOptions === "string") sid = sessionIdOrOptions;
      const sess = sessionsRef.current.get(sid || "");
      if (!sess) return;
      if (sess.controller) {
        sess.controller.abort();
        sess.controller = null;
      }
      sess.phase = "idle";
      sess.thinkingText = "";
      sess.stalled = false;
      sess.thinkingSince = null;
      // 用户主动中断：把仍在 running 的工具卡标 interrupted，避免“执行中…”永久残留
      resetRunningTools(sess);
      publishNow(sess.id);
    },
    [publishNow, resetRunningTools]
  );

  const reset = useCallback(
    (sessionId) => {
      const sid = typeof sessionId === "string" ? sessionId : activeIdRef.current;
      const sess = sessionsRef.current.get(sid || "");
      if (!sess) return;
      if (sess.controller) {
        sess.controller.abort();
        sess.controller = null;
      }
      const fresh = createSessionState(sess.id);
      sessionsRef.current.set(sess.id, fresh);
      publishNow(fresh.id);
    },
    [publishNow]
  );

  /** 覆盖指定会话的消息列表（编辑/重新生成/删除场景）。 */
  const setHistory = useCallback(
    (history, sessionId) => {
      const sid = typeof sessionId === "string" ? sessionId : activeIdRef.current;
      const sess = getSession(sid);
      sess.messages = Array.isArray(history) ? history : [];
      sess.phase = "idle";
      sess.thinkingText = "";
      sess.error = null;
      sess.uiBlocks = [];
      sess.currentAssistantId = null;
      sess.toolIndex = new Map();
      sess.hydrated = true;
      // 复位 P1 状态
      sess.reasoningText = "";
      sess.statusLine = "";
      sess.statusKind = "";
      sess.toolStatus = {};
      sess.subagents = [];
      sess.moaRefs = [];
      sess.moaAggregating = null;
      sess.reviewSummary = null;
      sess.usage = null;
      sess.browserProgress = [];
      publishNow(sess.id);
    },
    [getSession, publishNow]
  );

  /**
   * 从存储装载会话历史。
   *
   * **不再"一次性封 bucket"**：早期版本在首次成功后即设置 `sess.hydrated=true`，
   * 任何后续相同的 hydrate 调用都被硬跳过。但 tab 切换的 race 下，effect 第一次
   * 跑时 stored 可能是空数组（session 还没进内存里的 sessions 列表），bucket 被
   * 用空数据"封死"；等 sessions 异步补齐、effect 再跑、stored 是真实消息时，
   * 已被锁掉，UI 一直空白直到下一次 setHistory 才恢复。
   *
   * 现在改成基于内容 signature 的幂等：已 hydrate 但内容完全一致 → 跳过；
   * 已 hydrate 但内容不一致 → 用真数据重新填充；未 hydrate → 正常装载。
   * 仍然守 `phase !== "idle"`：避免把正在流的 bucket 用 on-disk 快照冲掉。
   */
  const hydrateSession = useCallback(
    (sessionId, stored) => {
      if (!sessionId) return false;
      const sess = getSession(sessionId);
      // Don't overwrite a live/streaming bucket — the stream has already
      // accumulated deltas that supersede anything on disk.
      if (sess.phase !== "idle") return false;
      const incoming = Array.isArray(stored) ? stored.map((m) => ({ ...m })) : [];
      // Cheap content fingerprint: id of the last message + total content length.
      // Cheap enough to run on every effect re-fire; unique enough to distinguish
      // empty vs real sessions and "stored snapshot changed" from "same snapshot".
      const sig = (arr) => {
        if (!arr || arr.length === 0) return "0";
        const last = arr[arr.length - 1];
        let len = 0;
        for (const m of arr) len += (m && m.content ? String(m.content).length : 0);
        return `${arr.length}:${last && last.id ? last.id : ""}:${len}`;
      };
      if (sess.hydrated && sig(sess.messages) === sig(incoming)) {
        // Snapshot unchanged — avoid the pointless republish that was making
        // every sessions refresh flicker the UI.
        return false;
      }
      // Accept empty incoming only if the in-memory bucket is also empty;
      // never replace a non-empty live bucket with empty (protects against
      // late "session not found yet" effect runs that race with the real load).
      if (incoming.length === 0 && sess.messages.length > 0 && sess.hydrated) {
        return false;
      }
      sess.messages = incoming;
      sess.hydrated = true;
      publishNow(sess.id);
      return true;
    },
    [getSession, publishNow]
  );

  /** 丢弃某会话的全部内存状态（会话被删除时调用）。 */
  const dropSession = useCallback(
    (sessionId) => {
      const key = sessionId || "";
      const sess = sessionsRef.current.get(key);
      if (!sess) return;
      if (sess.controller) sess.controller.abort();
      sessionsRef.current.delete(key);
      publishNow(key);
    },
    [publishNow]
  );

  /** 读取任意会话的消息（供切换时 flush 持久化使用）。 */
  const getSessionMessages = useCallback((sessionId) => {
    const sess = sessionsRef.current.get(sessionId || "");
    return sess ? sess.messages : null;
  }, []);

  /** 新建会话首发：把临时空会话("")的状态迁移到真实 session id 下。 */
  const adoptSessionId = useCallback(
    (fromId, toId) => {
      const from = sessionsRef.current.get(fromId || "");
      if (!from || !toId || fromId === toId) return;
      from.id = toId;
      sessionsRef.current.delete(fromId || "");
      sessionsRef.current.set(toId, from);
      publishNow(toId);
    },
    [publishNow]
  );

  // 卸载：中止全部在跑的流
  useEffect(() => {
    const sessions = sessionsRef.current;
    return () => {
      for (const s of sessions.values()) {
        if (s.controller) s.controller.abort();
      }
    };
  }, []);

  // Stall detection：全局扫描所有会话，thinking 超 60s 无事件即标记
  useEffect(() => {
    const timer = setInterval(() => {
      const now = Date.now();
      let changed = false;
      for (const s of sessionsRef.current.values()) {
        if (s.phase === "thinking" && s.thinkingSince && now - s.thinkingSince > STALL_THRESHOLD_MS) {
          if (!s.stalled) {
            s.stalled = true;
            dirtyRef.current.add(s.id);
            changed = true;
          }
        } else if (s.stalled && s.phase !== "thinking") {
          s.stalled = false;
          dirtyRef.current.add(s.id);
          changed = true;
        }
      }
      if (changed) flush();
    }, STALL_CHECK_INTERVAL);
    return () => clearInterval(timer);
  }, [flush]);

  return {
    messages: snapshot.messages,
    phase: snapshot.phase,
    thinkingText: snapshot.thinkingText,
    error: snapshot.error,
    uiBlocks: snapshot.uiBlocks,
    isStreaming: snapshot.phase !== "idle",
    stalled: snapshot.stalled,
    // ── P1 新增 ──
    reasoningText: snapshot.reasoningText,
    statusLine: snapshot.statusLine,
    statusKind: snapshot.statusKind,
    toolStatus: snapshot.toolStatus,
    subagents: snapshot.subagents,
    moaRefs: snapshot.moaRefs,
    moaAggregating: snapshot.moaAggregating,
    usage: snapshot.usage,
    reviewSummary: snapshot.reviewSummary,
    browserProgress: snapshot.browserProgress,
    runningSessionIds,
    sendMessage,
    stop,
    reset,
    setHistory,
    hydrateSession,
    dropSession,
    getSessionMessages,
    adoptSessionId,
  };
}
