import React, { useState, useEffect, useRef } from "react";
import Icon from "./Icon.jsx";
import TerminalPanel from "./TerminalPanel.jsx";

/**
 * ToolCard — 工具调用卡片
 * 折叠卡片：图标 + 工具名 + 参数摘要，展开显示完整 args / result
 * 支持终端风格 chunk 输出
 */
// Truncate a long result/args blob so an expanded card never fills the
// whole screen (spec §2: tool_end collapses the panel — the card stays a box).
const MAX_RESULT_CHARS = 2000;
const MAX_ARGS_CHARS = 800;

function toText(value) {
  if (value === undefined || value === null || value === "") return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function truncate(text, max) {
  if (!text) return { text: "", truncated: false };
  if (text.length <= max) return { text, truncated: false };
  return { text: text.slice(0, max), truncated: true };
}

export default function ToolCard({
  toolName = "tool",
  args,
  result,
  status = "complete", // running | complete | error
  durationMs,
  chunks = [],
  defaultExpanded = false,
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  // Spec §2: tool_end collapses the terminal panel. Once a card leaves the
  // running state, snap it shut unless the user manually re-opened it.
  const userToggledRef = useRef(false);
  useEffect(() => {
    if (status !== "running" && !userToggledRef.current) {
      setExpanded(false);
    }
  }, [status]);

  const statusIcon = status === "running" ? "settings" : status === "error" ? "close" : "check-circle";
  const statusColor = status === "running" ? "var(--warning)" : status === "error" ? "var(--error)" : "var(--success)";
  const argsText = toText(args);
  // Clean args summary: hide internal fields like tool_id, call_*, __
  const cleanArgs = argsText
    .replace(/"tool_id"\s*:\s*"[^"]*"\s*,?\s*/g, "")
    .replace(/"call_[a-f0-9]+"[^,]*,?/g, "")
    .replace(/"__\w+"[^,]*,?/g, "")
    .replace(/\s+/g, " ").trim();
  const argsSummary = cleanArgs ? cleanArgs.slice(0, 50) : "";
  const hasChunks = chunks && chunks.length > 0;

  const argsCut = truncate(argsText, MAX_ARGS_CHARS);
  const resultCut = truncate(toText(result), MAX_RESULT_CHARS);

  return (
    <div className="tool-card" data-status={status}>
      <button
        className="tool-card-header"
        onClick={() => { userToggledRef.current = true; setExpanded(!expanded); }}
        aria-expanded={expanded}
      >
        <span className="tool-card-icon"><Icon name={statusIcon} size={14} /></span>
        <span className="tool-card-name">{toolName}</span>
        {argsSummary && <span className="tool-card-args">{argsSummary}</span>}
        {durationMs !== undefined && (
          <span className="tool-card-duration" style={{ color: statusColor }}>
            {status === "error" ? "Failed" : "Completed"} in {(durationMs / 1000).toFixed(1)}s
          </span>
        )}
        <span className="tool-card-chevron"><Icon name="chevron" size={12} style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }} /></span>
      </button>
      {expanded && (
        <div className="tool-card-body">
          {hasChunks && (
            <TerminalPanel lines={chunks} isStreaming={status === "running"} />
          )}
          {argsText && !hasChunks && (
            <div className="tool-card-section">
              <div className="tool-card-section-title">参数</div>
              <pre className="tool-card-code">{argsCut.text}</pre>
              {argsCut.truncated && (
                <div className="tool-card-truncated">参数过长，已截断（前 {MAX_ARGS_CHARS} 字符）</div>
              )}
            </div>
          )}
          {resultCut.text && (
            <div className="tool-card-section">
              <div className="tool-card-section-title">结果</div>
              <pre className="tool-card-code">{resultCut.text}</pre>
              {resultCut.truncated && (
                <div className="tool-card-truncated">结果过长，已截断（前 {MAX_RESULT_CHARS} 字符）</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
