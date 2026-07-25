import React, { useState, useEffect, useCallback, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import AgentVerboseTimeline from "./AgentVerboseTimeline.jsx";
import ThinkingIndicator from "./ThinkingIndicator.jsx";
import StructuredThinking from "./StructuredThinking.jsx";
import TaskProgressPanel from "./TaskProgressPanel.jsx";
import ArtifactPreview from "./ArtifactPreview.jsx";
import ToolCard from "./ToolCard.jsx";
import TypewriterText from "./TypewriterText.jsx";
import useVirtualRows from "../hooks/useVirtualRows.js";
import bachAvatar from "../assets/bach-avatar.png";

function Lightbox({ src, alt, onClose }) {
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div className="lightbox-mask" onClick={onClose}>
      <img className="lightbox-img" src={src} alt={alt} onClick={(e) => e.stopPropagation()} />
    </div>
  );
}

function getTextFromNode(node) {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (!node) return "";
  if (Array.isArray(node)) return node.map(getTextFromNode).join("");
  if (node.props && node.props.children) return getTextFromNode(node.props.children);
  return "";
}

function CodeBlock({ children }) {
  const [copied, setCopied] = useState(false);
  const codeText = getTextFromNode(children).replace(/^\n+|\n+$/g, "");

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(codeText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error("copy failed", err);
    }
  };

  return (
    <div className="code-block-wrapper">
      <button className="code-copy-btn" onClick={handleCopy} title="复制">
        {copied ? "已复制" : "复制"}
      </button>
      <pre>{children}</pre>
    </div>
  );
}

export function sanitizeMessageContent(raw) {
  if (typeof raw !== "string") return raw;
  let t = raw;

  // Hide raw JSON/structured data leaks (LangGraph internal state fragments)
  const trimmed = t.trim();
  if (
    (/^[\{\[\"]/.test(trimmed) && /"[\w]+"\s*:/.test(trimmed)) ||
    (/\]\s*,?\s*$/.test(trimmed) && /"allowSteer|"messages|"tool_id|call_/.test(t))
  ) {
    return "";
  }

  t = t.replace(/\(¬[ _-]¬\)[♡\s]*cogitating\s*\.\.\./gi, "");
  t = t.replace(/\(¬[ _-]¬\)[♡\s]*reflecting\s*\.\.\./gi, "");
  t = t.replace(/◎[_ ]?◎\s*(mulling|reasoning|thinking|deliberating|pondering)\s*[-.]*/gi, "");
  t = t.replace(/◎◎\s*mulling\s*\.{2,}[\s\S]*?(?=\n|$)/gi, "");
  t = t.replace(/\([☺¬_◕▿•-]+\)\s*->\s*-+■{2,}\s*\w+\s*/gi, "");
  t = t.replace(/->\s*-+■{2,}\s*(analyzing|synthesizing|mulling|planning|executing|running|searching|calling)\b[\s\S]*?(?=\n|$)/gi, "");
  t = t.replace(/^(\([☺¬_◕▿-]+\)\s*[a-zA-Z]+\.{2,}\s*)+/gm, "");
  t = t.replace(/[\s.]{0,3}(me try using browser to this)\s*$/gi, "");
  t = t.replace(/\s*\.{2,3}\s*$/gm, "");
  t = t.replace(/[◎◯][_ ]?[◎◯]\s*[a-z]+\ing\b[-. ]*[^\n]*/gi, "");
  t = t.replace(/\n{3,}/g, "\n\n");
  t = t.split("\n").map((l) => l.trimEnd()).join("\n").trim();

  return t;
}

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

class MarkdownErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error, info) {
    console.error("ReactMarkdown render error:", error, info);
  }
  render() {
    if (this.state.hasError) {
      return <pre className="message-markdown fallback-text">{this.props.raw || ""}</pre>;
    }
    return this.props.children;
  }
}

function normalizeContentForTypewriter(content) {
  if (typeof content === "string") return content;
  if (content && typeof content === "object" && content.text) return content.text;
  if (Array.isArray(content)) {
    return content.map((p) => (typeof p === "string" ? p : p?.text || "")).join("");
  }
  return String(content || "");
}

function MarkdownView({ cleaned, onImageClick }) {
  return (
    <MarkdownErrorBoundary raw={cleaned}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        className="message-markdown"
        components={{
          pre: CodeBlock,
          img: ({ node, ...props }) => (
            <img
              {...props}
              style={{ maxWidth: "100%", cursor: "pointer", borderRadius: "6px" }}
              onClick={() => onImageClick && onImageClick(props.src, props.alt)}
              alt={props.alt || ""}
            />
          ),
        }}
      >
        {cleaned}
      </ReactMarkdown>
    </MarkdownErrorBoundary>
  );
}

// Collapse long assistant replies to a short preview with an expand toggle,
// so a big multi-section result doesn't flood the thread (简洁 + 可展开).
const COLLAPSE_CHAR_THRESHOLD = 600;
const COLLAPSE_LINE_THRESHOLD = 12;

function CollapsibleMarkdown({ cleaned, onImageClick }) {
  const [expanded, setExpanded] = useState(false);
  const lines = cleaned.split("\n");
  const tooLong = cleaned.length > COLLAPSE_CHAR_THRESHOLD || lines.length > COLLAPSE_LINE_THRESHOLD;

  if (!tooLong) {
    return <MarkdownView cleaned={cleaned} onImageClick={onImageClick} />;
  }

  const previewText = lines.slice(0, 6).join("\n");
  return (
    <div className={`collapsible-md ${expanded ? "expanded" : "collapsed"}`}>
      <div className="collapsible-md-content">
        <MarkdownView cleaned={expanded ? cleaned : previewText} onImageClick={onImageClick} />
      </div>
      {!expanded && <div className="collapsible-md-fade" />}
      <button className="collapsible-md-toggle" onClick={() => setExpanded(!expanded)}>
        {expanded ? "▴ 收起" : `▾ 展开全部（${lines.length} 行）`}
      </button>
    </div>
  );
}

function formatContent(content, onImageClick) {
  const cleaned = sanitizeMessageContent(content);
  if (typeof cleaned !== "string") {
    return <pre className="json-block">{JSON.stringify(content, null, 2)}</pre>;
  }
  if (!cleaned) return null;
  return <CollapsibleMarkdown cleaned={cleaned} onImageClick={onImageClick} />;
}

function mapStatus(s) {
  if (s === "running" || s === "in_progress") return "running";
  if (s === "error" || s === "failed") return "error";
  if (s === "pending") return "pending";
  return "complete";
}

function summarizeResult(res) {
  if (typeof res === "string") {
    const t = res.trim();
    if (!t) return "已返回结果";
    return t.length > 80 ? t.slice(0, 80) + "…" : t;
  }
  if (typeof res === "object") {
    if (Array.isArray(res)) return `返回 ${res.length} 项`;
    const keys = Object.keys(res || {});
    return keys.length ? `返回对象（${keys.length} 字段）` : "已返回结果";
  }
  return "已返回结果";
}

/**
 * Group consecutive tool messages into a single "tools" row.
 */
function renderGrouped(messages) {
  const rows = [];
  let pendingTools = [];

  function flushTools() {
    if (pendingTools.length > 0) {
      rows.push({
        type: "tools",
        items: [...pendingTools],
        allComplete: pendingTools.every(
          (t) => t.status !== "running" && t.status !== "in_progress" && t.status !== "error" && t.status !== "failed"
        ),
      });
      pendingTools = [];
    }
  }

  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (m.role === "tool") {
      pendingTools.push(m);
      const next = messages[i + 1];
      if (!next || next.role !== "tool") {
        flushTools();
      }
      continue;
    }
    flushTools();
    rows.push({ type: "message", data: m });
  }
  flushTools();

  return rows;
}

/**
 * Row key strategy per spec §5.3
 */
function rowKey(row, index) {
  if (row.type === "message") return row.data.id || `msg-${index}`;
  if (row.type === "tools") return `tools-${row.items[0]?.id || index}`;
  return "thinking";
}

/**
 * Estimated height per row type (spec §5.1)
 */
function estimatedHeight(row) {
  if (row.type === "tools") return 120;
  if (row.type === "thinking") return 48;
  return 80; // message
}

function ToolsRow({ items, assistantAvatar, loading, isLastRow, onImageClick }) {
  const toolsRunning = loading && isLastRow;
  const hasRunning = items.some(m => mapStatus(m.status) === "running");
  const allComplete = items.every(m => {
    const s = mapStatus(m.status);
    return s !== "running" && s !== "in_progress";
  });
  const totalTools = items.length;

  // Default expanded if running, or if there's only a single tool.
  const defaultExpanded = hasRunning || totalTools <= 1;
  const [expanded, setExpanded] = useState(defaultExpanded);

  // Keep expanded while any tool is running; collapse once all complete.
  useEffect(() => {
    if (hasRunning) setExpanded(true);
    else if (!userToggledRef.current && allComplete) setExpanded(false);
  }, [hasRunning, allComplete]);

  const userToggledRef = useRef(false);
  const toggle = () => {
    userToggledRef.current = true;
    setExpanded(v => !v);
  };

  const groups = {};
  for (const t of items) {
    const name = t.toolName || "tool";
    if (!groups[name]) groups[name] = [];
    groups[name].push(t);
  }
  const toolNames = Object.keys(groups);

  return (
    <div className="message-row assistant">
      <div className={`message-avatar agent-avatar ${toolsRunning ? "tool" : ""}`}>{assistantAvatar}</div>
      <div className="message-col">
        <div className="tool-summary-bar" onClick={toggle}>
          <span className="tool-summary-icon">{allComplete ? "✅" : "⚙️"}</span>
          <span className="tool-summary-text">
            {totalTools > 1 ? `${totalTools} 个工具调用` : (toolNames[0] || "工具")}
          </span>
          {totalTools > 1 && (
            <span className="tool-summary-detail">
              {toolNames.map(n => `${n}×${groups[n].length}`).join(" · ")}
            </span>
          )}
          <span className={`tool-summary-status ${allComplete ? "complete" : "running"}`}>
            {allComplete ? "全部完成" : "执行中…"}
          </span>
          <span className={`tool-summary-chevron ${expanded ? "expanded" : ""}`}>▸</span>
        </div>
        {expanded && (
          <div className="tool-group expanded">
            {items.map((m, i) => (
              <ToolCard
                key={m.id || `tool-${i}`}
                toolName={m.toolName || "tool"}
                args={m.args}
                result={m.result !== undefined && m.result !== null ? m.result : m.content}
                status={mapStatus(m.status)}
                durationMs={m.durationMs}
                defaultExpanded={mapStatus(m.status) === "running" || mapStatus(m.status) === "in_progress"}
              />
            ))}
          </div>
        )}
        <ArtifactPreview toolMessages={items} onImageClick={onImageClick} />
      </div>
    </div>
  );
}

export default function MessageThread({ messages = [], loading, streamPhase, thinkingText, onRetry, onRegenerate, assistant, manifests = [], onUpgradeToWorkbench }) {
  const [lightbox, setLightbox] = useState(null);

  // @ mention protocol (spec §2): a user message beginning with `@<name>`
  // is a sub-call into a workflow. We render it as an inner block and, for the
  // assistant reply that follows, offer an "open in workbench" upgrade (decision ①).
  function parseMentionToken(text) {
    const m = String(text || "").match(/^\s*@([^\s@]+)/);
    if (!m) return null;
    const token = m[1];
    const hit = manifests.find(
      (x) =>
        x.name === token ||
        x.id === token ||
        x.id === token.replace(/\s+/g, "_") ||
        x.name === token.replace(/_/g, " ")
    );
    return hit
      ? { id: hit.id, name: hit.name }
      : { id: token, name: token, unknown: true };
  }

  const mentionCtx = {};
  let lastMention = null;
  for (const m of messages) {
    if (m.role === "user") {
      const pm = parseMentionToken(m.content);
      lastMention = pm;
      if (pm) mentionCtx[m.id] = { self: pm };
    } else if (m.role === "assistant") {
      if (lastMention) mentionCtx[m.id] = { inner: lastMention };
    }
  }

  if (!Array.isArray(messages) || messages.length === 0) return null;
  const assistantAvatar = <img src={bachAvatar} alt="ABC" className="agent-avatar-img" />;
  const hasToolMsgs = messages.some((m) => m.role === "tool");

  // Build rows
  const grouped = renderGrouped(messages);
  const rows = [...grouped];
  // Show a working indicator whenever the agent is busy, unless the last row is
  // already an assistant message with visible streamed content or running tools.
  // For an EMPTY assistant message we inline the indicator inside that bubble;
  // for a user message we must still create an assistant thinking row (user rows
  // cannot host assistant thinking UI) — but we use a compact avatar-only style.
  if (loading) {
    const last = rows[rows.length - 1];
    const lastHasContent =
      last &&
      ((last.type === "message" && last.data.role === "assistant" &&
        sanitizeMessageContent(last.data.content) &&
        String(sanitizeMessageContent(last.data.content)).trim().length > 0) ||
        last.type === "tools");
    if (!lastHasContent && last) {
      if (last.type === "message" && last.data.role === "assistant") {
        last._thinking = true;
        last._phase = streamPhase;
      } else if (last.type === "message" && last.data.role === "user") {
        // User message cannot show assistant thinking; append a dedicated row.
        rows.push({ type: "thinking", phase: streamPhase });
      }
      // If last is "thinking" legacy row already, leave it as-is.
    }
  }

  // Virtual scroll
  const {
    containerRef,
    virtualItems,
    topSpacer,
    bottomSpacer,
    atBottom,
    scrollToBottom,
    measureRow,
    onScroll,
  } = useVirtualRows({ rows, estimatedHeight });

  const renderRow = (index) => {
    const row = rows[index];
    const isLastRow = index === rows.length - 1;

    if (row.type === "thinking") {
      // Dedicated assistant thinking row: shown when the last row is a user
      // message (user rows cannot host assistant thinking UI) or no host row.
      const phaseLabel =
        !thinkingText
          ? row.phase === "text_generating" ? "正在生成回复"
          : row.phase === "tool_executing" ? "正在调用工具"
          : "正在思考…"
          : "";
      const toolMessages = messages.filter((m) => m.role === "tool");
      return (
        <div className="message-row assistant">
          <div className="message-avatar agent-avatar thinking">{assistantAvatar}</div>
          <div className="message-col">
            <div className="message-bubble assistant">
              <TaskProgressPanel messages={toolMessages} thinkingText={thinkingText} isLoading={loading} />
              <StructuredThinking text={thinkingText} phaseLabel={phaseLabel} />
              <ArtifactPreview toolMessages={toolMessages} onImageClick={(src, alt) => setLightbox({ src, alt })} />
            </div>
          </div>
        </div>
      );
    }

    if (row.type === "tools") {
      return (
        <ToolsRow
          items={row.items}
          assistantAvatar={assistantAvatar}
          loading={loading}
          isLastRow={isLastRow}
          onImageClick={(src, alt) => setLightbox({ src, alt })}
        />
      );
    }

    // Regular message
    const m = row.data;
    const isUser = m.role === "user";
    const isError = m.isError;
    const isLast = isLastRow;
    const isStreamingText = isLast && !isUser && loading && streamPhase === "text_generating";
    const isThinkingInline = !isUser && row._thinking; // inline thinking inside this bubble
    const mctx = mentionCtx[m.id];
    const isMentionUser = isUser && !!mctx?.self;
    const isInner = !isUser && !!mctx?.inner;
    const displayContent = isMentionUser
      ? String(m.content || "").replace(/^\s*@([^\s@]+)/, "").trim()
      : m.content;
    const toolMessages = messages.filter((msg) => msg.role === "tool");
    return (
      <div className={`message-row ${isUser ? "user" : "assistant"} ${isMentionUser ? "msg-mention" : ""} ${isInner ? "msg-inner" : ""}`}>
        {!isUser && <div className={`message-avatar agent-avatar ${isStreamingText ? "typing" : ""} ${isThinkingInline ? "thinking" : ""}`}>{assistantAvatar}</div>}
        <div className="message-col">
          <div className={`message-bubble ${isUser ? "user" : "assistant"} ${isError ? "error" : ""} ${isThinkingInline ? "bubble-thinking" : ""}`}>
            {!isUser && (
              <div className="message-meta">
                <span className="message-name">{assistant?.name || "ABC"}</span>
                <span className="message-time">{formatTime(m.createdAt)}</span>
              </div>
            )}
            {isMentionUser && (
              <div className="mention-call">
                <span className="mention-chip">@{mctx.self.name}</span>
                <span className="mention-call-label">子调用</span>
              </div>
            )}
            {isThinkingInline ? (
              // Inline thinking: show progress panels inside this bubble instead of a separate row
              <>
                <TaskProgressPanel messages={toolMessages} thinkingText={thinkingText} isLoading={loading} />
                <StructuredThinking text={thinkingText} phaseLabel={
                  !thinkingText
                    ? (row._phase === "text_generating" ? "正在生成回复"
                      : row._phase === "tool_executing" ? "正在调用工具"
                      : "正在思考…")
                    : ""
                } />
                <ArtifactPreview toolMessages={toolMessages} onImageClick={(src, alt) => setLightbox({ src, alt })} />
              </>
            ) : isStreamingText ? (
              <TypewriterText
                content={sanitizeMessageContent(normalizeContentForTypewriter(displayContent))}
                isStreaming={true}
                onImageClick={(src, alt) => setLightbox({ src, alt })}
              />
            ) : (
              formatContent(displayContent, (src, alt) => setLightbox({ src, alt }))
            )}
          </div>
          {isLast && !loading && (
            <div className="message-actions">
              {isUser && onRetry && (
                <button className="message-action-btn" onClick={() => onRetry(m)} title="重新发送这条消息">
                  重试
                </button>
              )}
              {!isUser && onRegenerate && (
                <button className="message-action-btn" onClick={onRegenerate} title="基于上一条问题重新生成">
                  重新生成
                </button>
              )}
              {isInner && onUpgradeToWorkbench && !isError && (
                <button
                  className="message-action-btn upgrade"
                  onClick={() => onUpgradeToWorkbench(mctx.inner.id)}
                  title="把这次子调用升级为独立工作台会话"
                >
                  在 {mctx.inner.name} 工作台打开 →
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="vs-container" ref={containerRef} onScroll={onScroll}>
      <div className="vs-content">
        {/* Top spacer: pushes visible rows down to correct scroll position */}
        <div style={{ height: topSpacer }} />

        {virtualItems.map(({ index, row }) => (
          <div
            key={rowKey(row, index)}
            ref={(el) => measureRow(index, el)}
            data-row-index={index}
          >
            {renderRow(index)}
          </div>
        ))}

        {/* Bottom spacer: fills remaining scroll height */}
        <div style={{ height: bottomSpacer }} />
      </div>

      {!atBottom && (
        <button className="scroll-bottom-btn" onClick={() => scrollToBottom(true)} title="回到底部">
          ↓ 回到底部
        </button>
      )}

      {lightbox && (
        <Lightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}
