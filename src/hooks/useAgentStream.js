import { useState, useRef, useCallback, useEffect } from "react";
import { emitContractEvent } from "../contract/eventBus.js";

/**
 * useAgentStream — 自建 SSE token 流状态机
 *
 * 直接消费 agui-server 的 /api/ag-ui/run SSE 端点，完全绕开 CopilotKit。
 * 事件格式（@ag-ui/encoder）：`data: {JSON.stringify(event)}\n\n`
 *
 * 维护状态：
 *   - messages: [{id, role, content, createdAt, toolName, args, result, status, durationMs}]
 *   - phase: "idle" | "thinking" | "tool_executing" | "text_generating"
 *   - thinkingText: 累积的 thinking.delta 文本（供 ThinkingIndicator 显示）
 *   - isStreaming: phase !== "idle"
 */
export function useAgentStream(aguiPort) {
  const [messages, setMessages] = useState([]);
  const [phase, setPhase] = useState("idle");
  const [thinkingText, setThinkingText] = useState("");
  const [error, setError] = useState(null);

  const abortRef = useRef(null);
  const runIdRef = useRef(null);
  // Thread/session id of the run currently streaming. Used as the eventBus key
  // so contract (workflow.*) events reach the workbenches subscribed via
  // useContractEvents(session.id).
  const sessionIdRef = useRef(null);
  // 当前流式 assistant 消息 id（TEXT_MESSAGE_* 生命周期内不变）
  const currentAssistantIdRef = useRef(null);
  // tool_call_id -> messages 数组索引
  const toolIndexRef = useRef(new Map());

  const reset = useCallback(() => {
    setMessages([]);
    setPhase("idle");
    setThinkingText("");
    setError(null);
    currentAssistantIdRef.current = null;
    toolIndexRef.current = new Map();
  }, []);

  const setHistory = useCallback((history) => {
    setMessages(Array.isArray(history) ? history : []);
    setPhase("idle");
    setThinkingText("");
    setError(null);
    currentAssistantIdRef.current = null;
    toolIndexRef.current = new Map();
  }, []);

  // ── 消息操作 helpers ────────────────────────────────────────
  const appendMessage = useCallback((msg) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const patchMessage = useCallback((id, patch) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m))
    );
  }, []);

  const appendToMessage = useCallback((id, delta) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === id ? { ...m, content: (m.content || "") + delta } : m
      )
    );
  }, []);

  // ── SSE 事件处理 ────────────────────────────────────────────
  const handleEvent = useCallback(
    (ev) => {
      if (!ev || !ev.type) return;
      const now = Date.now();

      switch (ev.type) {
        case "RUN_STARTED":
          runIdRef.current = ev.runId;
          setPhase("thinking");
          setThinkingText("");
          setError(null);
          break;

        case "TEXT_MESSAGE_START":
          if (ev.role === "assistant") {
            currentAssistantIdRef.current = ev.messageId;
            appendMessage({
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
            if (phase !== "text_generating") setPhase("text_generating");
            appendToMessage(ev.messageId, ev.delta);
          }
          break;

        case "TEXT_MESSAGE_END":
          if (ev.messageId) {
            patchMessage(ev.messageId, { streaming: false });
          }
          currentAssistantIdRef.current = null;
          break;

        case "TOOL_CALL_START": {
          setPhase("tool_executing");
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
          toolIndexRef.current.set(ev.toolCallId, toolMsg.id);
          appendMessage(toolMsg);
          break;
        }

        case "TOOL_CALL_ARGS": {
          const id = toolIndexRef.current.get(ev.toolCallId);
          if (id && ev.delta) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === id ? { ...m, args: (m.args || "") + ev.delta } : m
              )
            );
          }
          break;
        }

        case "TOOL_CALL_RESULT": {
          const id = toolIndexRef.current.get(ev.toolCallId);
          if (id) {
            patchMessage(id, { result: ev.content, content: ev.content });
          }
          break;
        }

        case "TOOL_CALL_END": {
          const id = toolIndexRef.current.get(ev.toolCallId);
          if (id) {
            patchMessage(id, {
              status: ev.failed ? "error" : "complete",
              durationMs: ev.durationMs,
            });
          }
          setPhase("text_generating");
          break;
        }

        case "CUSTOM":
          handleCustom(ev);
          break;

        case "RUN_ERROR":
          setError(ev.message || "运行出错");
          setPhase("idle");
          appendMessage({
            id: `error-${now}`,
            role: "assistant",
            content: ev.message || "运行出错",
            createdAt: now,
            isError: true,
          });
          break;

        case "RUN_FINISHED":
          setPhase("idle");
          setThinkingText("");
          break;

        default:
          break;
      }
    },
    [phase, appendMessage, patchMessage, appendToMessage]
  );

  const handleCustom = useCallback((ev) => {
    const { name, value } = ev;
    if (name === "stream.phase") {
      const p = value?.phase;
      if (p) setPhase(p);
    } else if (name === "thinking.delta") {
      setPhase("thinking");
      setThinkingText((prev) => prev + (value?.text || ""));
    } else if (name === "tool.chunk") {
      const { toolCallId, chunk } = value || {};
      const id = toolIndexRef.current.get(toolCallId);
      if (id && chunk) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === id
              ? { ...m, chunks: [...(m.chunks || []), chunk] }
              : m
          )
        );
      }
    } else if (name && name.startsWith("workflow.")) {
      // Contract layer (L5): relay progress / artifact / approval / done events
      // into the eventBus keyed by the run's threadId. The generic workbenches
      // (Blueprint / Timeline / ManjuCraft) subscribe via useContractEvents
      // (session.id) and render real backend streams instead of mock data.
      emitContractEvent(sessionIdRef.current, { type: name, payload: value });
    }
  }, []);

  // ── 发送消息（建立 SSE 连接）────────────────────────────────
  const sendMessage = useCallback(
    async (text, { threadId, assistantId, skillId, model, history, mentions } = {}) => {
      if (!aguiPort) {
        setError("aguiPort 未就绪");
        return;
      }
      if (abortRef.current) {
        abortRef.current.abort();
      }

      const runId = `run-${Date.now()}`;
      // Remember the session/thread id so handleCustom can route workflow.*
      // events into the eventBus keyed by the same id the workbenches use.
      sessionIdRef.current = threadId || `thread-${Date.now()}`;
      const userMsg = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        createdAt: Date.now(),
      };
      appendMessage(userMsg);
      setPhase("thinking");
      setThinkingText("");
      setError(null);

      const controller = new AbortController();
      abortRef.current = controller;

      // 发送给后端的消息历史（含刚追加的 user 消息）
      const outgoing = [
        ...(history || []),
        { id: userMsg.id, role: "user", content: text },
      ];

      try {
        const res = await fetch(`http://127.0.0.1:${aguiPort}/api/ag-ui/run`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            method: "agent/run",
            threadId: threadId || `thread-${Date.now()}`,
            runId,
            messages: outgoing,
            forwardedProps: { assistantId, skillId, model, mentions: mentions || [] },
          }),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          throw new Error(`HTTP ${res.status}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
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
              handleEvent(JSON.parse(json));
            } catch {
              // 忽略单帧解析错误
            }
          }
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          setError(err.message || String(err));
          setPhase("idle");
        }
      } finally {
        abortRef.current = null;
      }
    },
    [aguiPort, appendMessage, handleEvent]
  );

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setPhase("idle");
    setThinkingText("");
  }, []);

  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  return {
    messages,
    phase,
    thinkingText,
    error,
    isStreaming: phase !== "idle",
    sendMessage,
    stop,
    reset,
    setHistory,
  };
}
