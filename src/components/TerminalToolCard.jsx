import React, { useState, useEffect, useRef, useCallback } from "react";
import Icon from "./Icon.jsx";
import { Terminal } from "xterm";
import { FitAddon } from "@xterm/addon-fit";
import "xterm/css/xterm.css";

/**
 * TerminalToolCard — 交互式终端工具卡片
 *
 * 渲染 agent 以 terminal(pty=True, background=True) 启动的后台 PTY 会话：
 * - 输出：useAgentStream 把 Hermes 的 agent.terminal.output 原始 ANSI chunk
 *   按 process_id 路由到消息的 terminalChunks，本组件逐段写入 xterm 实例；
 * - 输入：interactive 时 xterm.onData → gatewayRequest("process.write")，
 *   用户可直接敲键盘与运行中的 TUI / PowerShell 进程交互；
 * - 关闭：调用现成的网关方法 process.kill（session 隔离校验在 Hermes 侧）。
 *
 * 视觉外壳与 ToolCard 一致（图标 + 工具名 + 状态 + 折叠），展开体是 xterm。
 */
const TERM_THEME = {
  background: "#0d1117",
  foreground: "#e6edf3",
  cursor: "#e6edf3",
  cursorAccent: "#0d1117",
  selectionBackground: "rgba(88,166,255,0.25)",
  black: "#0d1117",
  red: "#ff7b72",
  green: "#3fb950",
  yellow: "#d29922",
  blue: "#58a6ff",
  magenta: "#bc8cff",
  cyan: "#39c5cf",
  white: "#e6edf3",
  brightBlack: "#484f58",
  brightRed: "#ffa198",
  brightGreen: "#56d364",
  brightYellow: "#e3b341",
  brightBlue: "#79c0ff",
  brightMagenta: "#d2a8ff",
  brightCyan: "#56d4dd",
  brightWhite: "#f0f6fc",
};

const MAX_SCROLLBACK = 2000;

function toText(value) {
  if (value === undefined || value === null || value === "") return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export default function TerminalToolCard({
  toolName = "terminal",
  status = "complete", // running | complete | error | interrupted
  result,
  durationMs,
  generating = false,
  terminalChunks = [],
  processId = null,
  interactive = false,
  terminalClosed = false,
  defaultExpanded = false,
  onClose,
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const userToggledRef = useRef(false);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const hostRef = useRef(null);
  const writtenRef = useRef(0);

  const isRunning = status === "running";
  const ended = terminalClosed || status === "complete" || status === "error" || status === "interrupted";

  // ── Create the xterm instance once, write any pre-mount backlog ──
  useEffect(() => {
    if (!hostRef.current) return;
    const term = new Terminal({
      fontSize: 12,
      lineHeight: 1.25,
      fontFamily: "Consolas, 'Courier New', monospace",
      theme: TERM_THEME,
      scrollback: MAX_SCROLLBACK,
      cursorBlink: true,
      allowProposedApi: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    try {
      fit.fit();
    } catch {
      /* container may be hidden; refit on expand */
    }
    termRef.current = term;
    fitRef.current = fit;
    const pending = terminalChunks.slice(0);
    writtenRef.current = pending.length;
    pending.forEach((c) => term.write(c));
    return () => {
      try {
        term.dispose();
      } catch {
        /* noop */
      }
      termRef.current = null;
      fitRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Append newly arrived raw ANSI chunks ──
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenRef.current; i < terminalChunks.length; i++) {
      term.write(terminalChunks[i]);
    }
    writtenRef.current = terminalChunks.length;
  }, [terminalChunks]);

  // ── Refit + focus on expand ──
  useEffect(() => {
    if (expanded && fitRef.current) {
      try {
        fitRef.current.fit();
      } catch {
        /* noop */
      }
      if (interactive && processId && termRef.current) termRef.current.focus();
    }
  }, [expanded, interactive, processId]);

  // ── Keyboard input → Hermes process.write gateway method ──
  useEffect(() => {
    const term = termRef.current;
    if (!term || !interactive || !processId || ended) return;
    const disposable = term.onData((data) => {
      window.hermes
        ?.gatewayRequest?.("process.write", { process_id: processId, data }, 15000)
        .catch((err) => {
          console.warn("[terminal] process.write failed:", err);
        });
    });
    return () => disposable.dispose();
  }, [interactive, processId, ended]);

  // ── Resize → Hermes process.resize gateway method (200ms throttle) ──
  const resizeTimerRef = useRef(null);
  useEffect(() => {
    const term = termRef.current;
    if (!term || !interactive || !processId || ended) return;
    const disposable = term.onResize(({ cols, rows }) => {
      if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
      resizeTimerRef.current = setTimeout(() => {
        window.hermes
          ?.gatewayRequest?.("process.resize", { process_id: processId, cols, rows }, 15000)
          .catch((err) => {
            console.warn("[terminal] process.resize failed:", err);
          });
      }, 200);
    });
    return () => {
      disposable.dispose();
      if (resizeTimerRef.current) {
        clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = null;
      }
    };
  }, [interactive, processId, ended]);

  const statusIcon = isRunning ? "settings" : status === "error" ? "close" : status === "interrupted" ? "stop" : "check-circle";
  const statusColor = isRunning ? "var(--warning)" : status === "error" ? "var(--error)" : status === "interrupted" ? "var(--muted, #888)" : "var(--success)";
  const resultText = toText(result);

  const handleClose = useCallback(() => {
    if (onClose) {
      onClose();
      return;
    }
    if (!processId) return;
    window.hermes
      ?.gatewayRequest?.("process.kill", { process_id: processId }, 15000)
      .catch((err) => console.warn("[terminal] process.kill failed:", err));
  }, [processId, onClose]);

  return (
    <div className="terminal-tool-card" data-status={status}>
      <button
        className="tool-card-header"
        onClick={() => {
          userToggledRef.current = true;
          setExpanded((v) => !v);
        }}
        aria-expanded={expanded}
      >
        <span className="tool-card-icon"><Icon name="terminal" size={14} /></span>
        <span className="tool-card-name">{toolName}</span>
        {isRunning && <span className="tool-card-generating"><Icon name="settings" size={11} /> 运行中</span>}
        {durationMs !== undefined && !isRunning && (
          <span className="tool-card-duration" style={{ color: statusColor }}>
            {status === "error" ? "Failed" : status === "interrupted" ? "Interrupted" : "Completed"} in {(durationMs / 1000).toFixed(1)}s
          </span>
        )}
        {!isRunning && ended && (
          <span className="tool-card-duration" style={{ color: statusColor }}>已结束</span>
        )}
        <span className="tool-card-chevron"><Icon name="chevron" size={12} style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }} /></span>
      </button>
      {expanded && (
        <div className="terminal-tool-card-body">
          <div className="terminal-tool-statusbar">
            <span className={`terminal-tool-status ${isRunning ? "running" : "ended"}`}>
              <span className={`arm-status-icon ${isRunning ? "task-running" : "task-completed"}`}>
                <Icon name={isRunning ? "terminal" : "check-circle"} size={12} />
              </span>
              {isRunning ? "终端会话运行中 — 可直接输入" : "终端会话已结束（只读）"}
            </span>
            {(interactive || processId) && (
              <button className="terminal-tool-close" onClick={handleClose} title="关闭终端会话" disabled={ended}>
                <Icon name="close" size={12} /> 关闭
              </button>
            )}
          </div>
          <div className="xterm-host" ref={hostRef} />
          {resultText && (
            <div className="tool-card-section">
              <div className="tool-card-section-title">结果</div>
              <pre className="tool-card-code">{resultText}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
