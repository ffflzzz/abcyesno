import { useState, useRef, useCallback, useEffect } from "react";
import { emitContractEvent } from "../contract/eventBus.js";

// ── Agent 自渲染 UI 组件：白名单 + blockId 校验 ──
const UI_BLOCK_TYPES = new Set(["table", "flowchart", "card", "progress", "action"]);
const BLOCK_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/;
// 渲染上限，防恶意大 payload 卡死渲染（spec §6.4）
const UI_BLOCK_MAX = 64;

// Stall detection
const STALL_CHECK_INTERVAL = 5000;
const STALL_THRESHOLD_MS = 60000;
// SSE reader idle timeout
const READ_TIMEOUT_MS = 120_000;

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
    // 运行时内部状态
    controller: null,
    runId: null,
    currentAssistantId: null,
    toolIndex: new Map(),
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
  };
}

function sameIds(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
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
        // Filter kawaii spinner noise from conversation_loop (e.g. "(◎_◎) mulling...").
        // Real reasoning content arrives via reasoning.delta (forwarded in agui-server).
        if (/^[\s\p{P}\p{S}]{1,6}\s+\w+\.\.\.$/u.test(text.trim())) return;
        sess.phase = "thinking";
        sess.thinkingText += text;
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
      } else if (name && name.startsWith("workflow.")) {
        // Contract layer (L5): relay progress / artifact / approval / done events
        // into the eventBus keyed by the run's threadId. The generic workbenches
        // subscribe via useContractEvents(session.id).
        emitContractEvent(sess.id, { type: name, payload: value });
      } else if (name === "ui.render") {
        // Agent 自渲染 UI 组件能力（spec: AGENT_UI_RENDER_SPEC.md §3.1）。
        // 安全：未知 type 或非法 blockId 静默丢弃（§6）。
        const { blockId, type, props, replace, appendPreview } = value || {};
        if (!type || !UI_BLOCK_TYPES.has(type)) return;
        if (!blockId || !BLOCK_ID_RE.test(blockId)) return;
        const safeProps = props && typeof props === "object" ? props : {};
        const prev = sess.uiBlocks;
        const block = { blockId, type, props: safeProps };
        const idx = prev.findIndex((b) => b.blockId === blockId);
        if (idx >= 0) {
          // 幂等更新：相同 blockId 直接替换（action 流式进度由 agent 发累积 props）。
          // 若声明 appendPreview 且旧块已有 preview，则仅追加 preview 文本。
          const next = prev.slice();
          if (
            appendPreview &&
            typeof next[idx].props?.preview === "string" &&
            typeof safeProps.preview === "string"
          ) {
            next[idx] = {
              ...block,
              props: { ...safeProps, preview: next[idx].props.preview + safeProps.preview },
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
    [publish]
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
          if (ev.messageId) patchMessage(sess, ev.messageId, { streaming: false });
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
          if (id) patchMessage(sess, id, { result: ev.content, content: ev.content });
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
    async (text, { threadId, assistantId, skillId, model, history, mentions } = {}) => {
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

      // 历史优先取本会话自己的消息（调用方可显式覆盖）
      const priorHistory =
        history ||
        sess.messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({ id: m.id, role: m.role, content: m.content }));

      sess.messages = [...sess.messages, userMsg];
      sess.phase = "thinking";
      sess.thinkingText = "";
      sess.error = null;
      sess.stalled = false;
      sess.thinkingSince = Date.now();
      sess.uiBlocks = []; // 新的一轮：清空上一轮的 agent 自渲染组件
      publishNow(sess.id);

      const controller = new AbortController();
      sess.controller = controller;

      const outgoing = [...priorHistory, { id: userMsg.id, role: "user", content: text }];

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
            forwardedProps: { assistantId, skillId, model, mentions: mentions || [] },
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
          const timer = setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
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
            publish(sess.id);
            settle(sess);
          }
        }
        syncRunning();
      }
    },
    [aguiPort, getSession, publish, publishNow, handleEvent, settle, syncRunning]
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
      publishNow(sess.id);
    },
    [publishNow]
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
      publishNow(sess.id);
    },
    [getSession, publishNow]
  );

  /**
   * 从存储装载会话历史。**若该会话正在流式运行或已装载过，则不覆盖**——
   * 这是多会话并发的关键：切回一个后台仍在跑的会话时，必须保留内存中
   * 已累积的增量，而不是用磁盘上的旧快照把它冲掉。
   * @returns true 表示实际写入了历史
   */
  const hydrateSession = useCallback(
    (sessionId, stored) => {
      const sess = getSession(sessionId);
      if (sess.hydrated || sess.phase !== "idle" || sess.messages.length > 0) {
        // Already live (or already loaded). Do NOT re-publish — this effect
        // re-runs whenever the sessions array identity changes, and an extra
        // snapshot would cause a pointless re-render on every list refresh.
        return false;
      }
      sess.messages = Array.isArray(stored) ? stored.map((m) => ({ ...m })) : [];
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
