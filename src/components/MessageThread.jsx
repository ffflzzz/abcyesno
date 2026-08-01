import React, { useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo } from "react";
import Icon from "./Icon.jsx";
import { Virtuoso } from "react-virtuoso";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import AgentVerboseTimeline from "./AgentVerboseTimeline.jsx";
import ThinkingIndicator from "./ThinkingIndicator.jsx";
import ArtifactPreview from "./ArtifactPreview.jsx";
import ToolCard from "./ToolCard.jsx";
import TypewriterText from "./TypewriterText.jsx";
import ApprovalBubble from "./ApprovalBubble.jsx";
import GeneratedComponent from "./GeneratedComponent.jsx";
import TableBlock from "./ui/TableBlock.jsx";
import MessageActions from "./MessageActions.jsx";
import { useContractEvents } from "../hooks/useContractEvents.js";
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

// ── sanitizeMessageContent cache ──────────────────────────────
// Historical/finalized messages have stable content, but the function runs
// ~15 regexes per call and was re-invoked for every message on every stream
// token (full-column re-render). Cache results keyed by raw string so repeated
// renders of unchanged content hit O(1) instead of re-running all regexes.
const sanitizeCache = new Map();
const SANITIZE_CACHE_MAX = 1500;

export function sanitizeMessageContent(raw) {
  if (typeof raw !== "string") return raw;
  const cached = sanitizeCache.get(raw);
  if (cached !== undefined) return cached;
  const result = sanitizeImpl(raw);
  if (sanitizeCache.size > SANITIZE_CACHE_MAX) sanitizeCache.clear();
  sanitizeCache.set(raw, result);
  return result;
}

function sanitizeImpl(raw) {
  let t = raw;

  // Suppress Hermes interrupt metadata that leaked into stored messages.
  // These are internal status strings, never user-visible content.
  if (/^Operation interrupted:/.test(t.trim())) return "";

  // Hide raw JSON/structured data leaks (LangGraph internal state fragments)
  const trimmed = t.trim();
  if (
    (/^[\{\[\"]/.test(trimmed) && /"[\w]+"\s*:/.test(trimmed)) ||
    (/\]\s*,?\s*$/.test(trimmed) && /"allowSteer|"messages|"tool_id|call_/.test(t))
  ) {
    return "";
  }

  // Strip outer code fences that models sometimes wrap entire responses in.
  // E.g. "```markdown\n1. **step** ...\n```" → "1. **step** ..."
  // Only strip if the fenced content looks like prose/markdown, not real code.
  const fenceMatch = t.match(/^(`{3,})(\w*)\n([\s\S]+?)\n\s*\1\s*$/);
  if (fenceMatch) {
    const lang = (fenceMatch[2] || "").toLowerCase();
    const inner = fenceMatch[3].trim();
    // If language tag is markdown/text/empty AND inner looks like prose (not pure JSON/code)
    if ((!lang || ["markdown", "md", "text", "txt"].includes(lang)) &&
        !/^[\{\[<]/.test(inner) &&
        inner.length > 20) {
      t = inner;
    }
  }

  // ════════════════════════════════════════
  // Phase 1: Strip ENTIRE thinking/reasoning BLOCKS
  // Models output thinking as kaomoji-prefixed lines like:
  //   (○_○) reflecting...用户询问...
  //   (◎_◎) mulling...我看了配置...
  //   (╯‵□′)╯ reflecting...
  //   (¬_¬) cogitating...
  // These must be fully removed from displayed content so they ONLY
  // appear in ThinkingTranscript (which gets its own thinkingText prop).
  // ════════════════════════════════════════

  // Single-line: (kaomoji) verb... → remove whole line
  const thinkLineRe = /^\s*\([^)]{1,20}\)\s*(reflecting|cogitating|mulling|reasoning|thinking|deliberating|pondering|considering|analyzing|processing|contemplating|evaluating|assessing|planning|searching|investigating|exploring|examining)\b[\s\S]*$/gim;
  t = t.replace(thinkLineRe, "");

  // Multi-line block: (kaomoji) verb... \n continued thinking text
  // Stop at code fence, header, numbered list, or blank line boundary
  const thinkBlockRe = /^\s*\([^)]{1,20}\)\s*(reflecting|cogitating|mulling|reasoning|thinking|deliberating|pondering|considering|analyzing|processing|contemplating|evaluating|assessing|planning|searching|investigating|exploring|examining)\b[\s\S]*?(?=\n\s*(?:```|\n#{1,3}\s|\d+\.|\*|- |\n\n|$))/gim;
  t = t.replace(thinkBlockRe, "");

  // ════════════════════════════════════════
  // Phase 2: Legacy / fragment patterns (older model outputs)
  // ════════════════════════════════════════
  t = t.replace(/\(¬[ _-]¬\)[♡\s]*cogitating\s*\.\.\./gi, "");
  t = t.replace(/\(¬[ _-]¬\)[♡\s]*reflecting\s*\.\.\./gi, "");
  t = t.replace(/◎[_ ]?◎\s*(mulling|reasoning|thinking|deliberating|pondering)\s*[-.]*/gi, "");
  t = t.replace(/◎◎\s*mulling\s*\.{2,}[\s\S]*?(?=\n|$)/gi, "");
  t = t.replace(/\([☺¬_◕▿•-]+\)\s*->\s*-+■{2,}\s*\w+\s*/gi, "");
  t = t.replace(/->\s*-+■{2,}\s*(analyzing|synthesizing|mulling|planning|executing|running|searching|calling)\b[\s\S]*?(?=\n|$)/gi, "");
  t = t.replace(/^(\([☺¬_◕▿-]+\)\s*[a-zA-Z]+\.{2,}\s*)+/gm, "");
  t = t.replace(/[\s.]{0,3}(me try using browser to this)\s*$/gi, "");
  t = t.replace(/[◎◯][_ ]?[◎◯]\s*[a-z]+\ing\b[-. ]*[^\n]*/gi, "");

  // ════════════════════════════════════════
  // Phase 3: Clean up residuals
  // ════════════════════════════════════════
  t = t.replace(/\s*\.{2,3}\s*$/gm, "");          // trailing dots on lines
  t = t.replace(/\n{3,}/g, "\n\n");               // collapse 3+ blank lines
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
          table: ({ node, children, ...props }) => {
            // Try to extract structured data for TableBlock (MVP component).
            const extracted = extractTableData(node);
            if (extracted) {
              return <TableBlock columns={extracted.columns} rows={extracted.rows} />;
            }
            // Fallback: plain styled table.
            return (
              <div className="md-table-wrapper">
                <table {...props}>{children}</table>
              </div>
            );
          },
        }}
      >
        {cleaned}
      </ReactMarkdown>
    </MarkdownErrorBoundary>
  );
}

/** Extract columns + rows from a react-markdown table AST node. Returns null if not a data table. */
function extractTableData(node) {
  if (!node || !node.children) return null;
  let thead = null;
  let tbody = null;
  for (const child of node.children) {
    if (child.tagName === "thead") thead = child;
    if (child.tagName === "tbody") tbody = child;
  }
  if (!thead || !tbody) return null;
  // Extract header columns from <th> elements.
  const columns = [];
  const headerRow = thead.children?.find(c => c.tagName === "tr");
  if (headerRow?.children) {
    for (const cell of headerRow.children) {
      if (cell.tagName === "th" || cell.tagName === "td") {
        const text = extractTextFromAst(cell);
        if (text != null) columns.push(text);
      }
    }
  }
  if (columns.length === 0) return null;
  // Extract body rows from <tr> elements.
  const rows = [];
  for (const tr of tbody.children || []) {
    if (tr.tagName !== "tr") continue;
    // Skip separator rows (GFM tables have a row of dashes).
    if (tr.children?.every(c => {
      const t = extractTextFromAst(c);
      return t != null && /^[-:|]+$/.test(t.replace(/\s/g, ""));
    })) continue;
    const row = [];
    for (const cell of tr.children || []) {
      if (cell.tagName === "td" || cell.tagName === "th") {
        row.push(extractTextFromAst(cell) ?? "");
      }
    }
    if (row.length > 0) rows.push(row);
  }
  if (rows.length === 0) return null;
  return { columns, rows };
}

/** Recursively extract text content from a remark/rehype AST node. */
function extractTextFromAst(node) {
  if (!node) return null;
  if (typeof node.value === "string") return node.value;
  if (node.children?.length) {
    return node.children.map(extractTextFromAst).filter(Boolean).join("");
  }
  return null;
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
        {expanded ? (<><Icon name="chevron" size={12} style={{ transform: "rotate(-90deg)" }} /> 收起</>) : (<><Icon name="chevron" size={12} style={{ transform: "rotate(90deg)" }} /> 展开全部（{lines.length} 行）</>)}
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

/**
 * ThinkingTranscript — shows the model's real reasoning tokens (thinkingText)
 * streamed from the backend. Renders as inline streaming text (no card/bubble).
 * Auto-collapses when the agent moves past the thinking phase.
 */
function ThinkingTranscript({ text, collapsed = false }) {
  const ref = useRef(null);
  const pinnedRef = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinnedRef.current && !collapsed) el.scrollTop = el.scrollHeight;
  }, [text, collapsed]);

  if (!text || !text.trim()) return null;

  // When collapsed, show a small expandable hint line
  if (collapsed) {
    return (
      <div className="thinking-inline-collapsed">
        <span className="thinking-dim-icon">💭</span>
        <span className="thinking-dim-text">思考过程已折叠</span>
      </div>
    );
  }

  return (
    <div className="thinking-inline" ref={ref} onScroll={(e) => {
      const el = e.currentTarget;
      pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    }}>
      <div className="thinking-inline-text">{text}</div>
    </div>
  );
}

/**
 * EditBox — inline editor shown in place of a user message bubble when the
 * user clicks "编辑消息" in MessageActions. Mirrors ChatGPT behaviour:
 * editing a user message discards the old reply and re-sends with new text.
 *   - Enter      → save & send
 *   - Shift+Enter → newline
 *   - Esc        → cancel
 */
function EditBox({ initialText, onSave, onCancel }) {
  const [text, setText] = useState(initialText || "");
  const ref = useRef(null);
  const autoGrow = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 400) + "px";
  }, []);
  useEffect(() => {
    if (ref.current) {
      ref.current.focus();
      // Place cursor at end
      const len = ref.current.value.length;
      ref.current.setSelectionRange(len, len);
      autoGrow();
    }
  }, [autoGrow]);
  const handleChange = (e) => {
    setText(e.target.value);
    autoGrow();
  };
  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (text.trim()) onSave(text);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };
  return (
    <div className="msg-edit-box">
      <textarea
        ref={ref}
        className="msg-edit-textarea"
        value={text}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        rows={1}
      />
      <div className="msg-edit-actions">
        <button className="msg-edit-cancel" onClick={onCancel} title="取消 (Esc)">
          取消
        </button>
        <button
          className="msg-edit-save"
          onClick={() => text.trim() && onSave(text)}
          title="保存并发送 (Enter)"
        >
          <Icon name="send" size={12} />
          保存并发送
        </button>
      </div>
      <div className="msg-edit-hint">Enter 发送 · Shift+Enter 换行 · Esc 取消</div>
    </div>
  );
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

function ToolsRow({ items, assistantAvatar, loading, isLastRow, onImageClick, onViewInSidebar }) {
  const toolsRunning = loading && isLastRow;
  const hasRunning = items.some(m => mapStatus(m.status) === "running");
  const allComplete = items.every(m => {
    const s = mapStatus(m.status);
    return s !== "running" && s !== "in_progress";
  });
  const totalTools = items.length;

  // Always collapsed by default — user clicks to expand.
  const [expanded, setExpanded] = useState(false);

  // Never auto-expand; respect user toggle only.
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
          <span className="tool-summary-icon"><Icon name={allComplete ? "check-circle" : "settings"} size={14} /></span>
          <span className="tool-summary-text">
            {totalTools > 1 ? `${totalTools} 个工具调用` : (toolNames[0] || "工具")}
          </span>
          {totalTools > 1 && (
            <span className="tool-summary-detail">
              {toolNames.map(n => `${n}×${groups[n].length}`).join(" · ")}
            </span>
          )}
          <span
            className={`tool-summary-status ${allComplete ? "complete" : "running"}`}
            style={!allComplete ? {
              color: "#d29922",
              animation: "status-running-pulse 1.5s ease-in-out infinite",
            } : undefined}
          >
            {allComplete ? "全部完成" : "执行中…"}
          </span>
          <span className={`tool-summary-chevron ${expanded ? "expanded" : ""}`}><Icon name="chevron" size={12} style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }} /></span>
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
        <ArtifactPreview toolMessages={items} compact onViewInSidebar={onViewInSidebar} />
      </div>
    </div>
  );
}

export default function MessageThread({ messages = [], loading, streamPhase, thinkingText, uiBlocks = [], stalled = false, onRetry, onRegenerate, assistant, manifests = [], onUpgradeToWorkbench, onOpenPreviewUrl, approval, onRespondApproval, sessionId, onEditMessage, onDeleteMessage, editingMessageId, onSaveEdit, onCancelEdit }) {
  const [lightbox, setLightbox] = useState(null);

  // Only show tool messages from the current turn (after the last user message),
  // not the entire session history. Prevents stale tool calls from flooding
  // the TaskProgressPanel on every new response.
  const currentTurnToolMessages = useMemo(() => {
    let lastUserIdx = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") { lastUserIdx = i; break; }
    }
    if (lastUserIdx < 0) return messages.filter((m) => m.role === "tool");
    return messages.slice(lastUserIdx + 1).filter((m) => m.role === "tool");
  }, [messages]);

  // ── Workflow progress: extract latest running step from contract events ──
  const contractEvents = useContractEvents(sessionId);
  const latestProgress = useMemo(() => {
    if (!contractEvents || !contractEvents.length) return null;
    // Find the most recent workflow.progress event with status="running"
    let latest = null;
    for (let i = contractEvents.length - 1; i >= 0; i--) {
      const ev = contractEvents[i];
      if (ev.type === "workflow.progress" && ev.payload?.status === "running") {
        latest = ev.payload;
        break;
      }
    }
    // Fallback: if no running, show the last completed step
    if (!latest) {
      for (let i = contractEvents.length - 1; i >= 0; i--) {
        const ev = contractEvents[i];
        if (ev.type === "workflow.progress") { latest = ev.payload; break; }
      }
    }
    return latest;
  }, [contractEvents]);

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
    } else if (lastHasContent) {
      // Last row has content (tools or assistant text), but agent is still working.
      // Append a compact progress row so the user can see real-time step info.
      rows.push({ type: "thinking", phase: streamPhase || "tool_executing" });
    }
  }

  // ── Virtualized scroll via react-virtuoso ───────────────────
  // Long conversations previously rendered the entire message column in a
  // native overflow-y:auto container — every stream token re-rendered all
  // rows (O(N) DOM work). Virtuoso keeps only the visible window mounted.
  // Auto-follow pins to bottom while streaming but yields the moment the
  // user scrolls up (restored via Virtuoso's atBottomStateChange / followOutput).
  const virtuosoRef = useRef(null);
  const [atBottom, setAtBottom] = useState(true);

  const scrollToBottom = useCallback((smooth) => {
    if (virtuosoRef.current) {
      virtuosoRef.current.scrollToIndex({
        index: rows.length - 1,
        behavior: smooth ? "smooth" : "auto",
        align: "end",
      });
    }
    setAtBottom(true);
  }, [rows.length]);

  const renderRow = (index) => {
    const row = rows[index];
    const isLastRow = index === rows.length - 1;

    if (row.type === "thinking") {
      // Dedicated thinking row: compact indicator only. Detailed progress lives
      // in the header status bar and right-panel workflow timeline — NOT in chat bubbles.
      const phaseLabel =
        !thinkingText
          ? row.phase === "text_generating" ? "正在生成回复"
          : row.phase === "tool_executing" ? "正在调用工具"
          : "正在思考…"
          : "";
      return (
        <div className="message-row assistant">
          <div className="message-avatar agent-avatar thinking">{assistantAvatar}</div>
          <div className="message-col">
            <div className="assistant-body">
              <div className="bubble-thinking-compact">
                <div className="btc-line">
                  <span className="btc-spinner" />
                  <span className="btc-text">{phaseLabel || "处理中…"}</span>
                </div>
                {latestProgress && (
                  <div className="btc-progress">
                    <span className="btc-stage">{latestProgress.stage || latestProgress.step_id}</span>
                    {latestProgress.total > 0 && (
                      <span className="btc-pos">{latestProgress.completed}/{latestProgress.total}</span>
                    )}
                  </div>
                )}
                <ThinkingTranscript text={thinkingText} collapsed={!isLastRow || streamPhase === "text_generating"} />
                {stalled && (
                  <div className="btc-stall-warning">
                    <span className="btc-stall-icon">⚠</span>
                    <span>响应超时，后端可能未就绪。可点击停止后重试。</span>
                  </div>
                )}
              </div>
              <ArtifactPreview toolMessages={currentTurnToolMessages} compact onViewInSidebar={() => onOpenPreviewUrl && onOpenPreviewUrl("tab:artifacts")} />
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
          onViewInSidebar={() => onOpenPreviewUrl && onOpenPreviewUrl("tab:artifacts")}
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
    const isEditing = editingMessageId && m.id === editingMessageId;
    const mctx = mentionCtx[m.id];
    const isMentionUser = isUser && !!mctx?.self;
    const isInner = !isUser && !!mctx?.inner;
    const displayContent = isMentionUser
      ? String(m.content || "").replace(/^\s*@([^\s@]+)/, "").trim()
      : m.content;
    const toolMessages = currentTurnToolMessages;
    return (
      <div className={`message-row ${isUser ? "user" : "assistant"} ${isMentionUser ? "msg-mention" : ""} ${isInner ? "msg-inner" : ""}`}>
        {!isUser && <div className={`message-avatar agent-avatar ${isStreamingText ? "typing" : ""} ${isThinkingInline ? "thinking" : ""}`}>{assistantAvatar}</div>}
        <div className="message-col">
          <div className={`${isUser ? "message-bubble user" : "assistant-body"} ${isError ? "error" : ""} ${isThinkingInline ? "bubble-thinking" : ""}`}>
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
            {isEditing ? (
              <EditBox
                initialText={typeof m.content === "string" ? m.content : normalizeContent(displayContent)}
                onSave={(text) => onSaveEdit(m.id, text)}
                onCancel={onCancelEdit}
              />
            ) : isThinkingInline ? (
              // Inline thinking: compact indicator only. No TPP/ST — those
              // belong in the header bar and workflow sidebar, not chat bubbles.
              <>
                <div className="bubble-thinking-compact">
                  <div className="btc-line">
                    <span className="btc-spinner" />
                    <span className="btc-text">{
                      row._phase === "text_generating" ? "正在生成回复"
                      : row._phase === "tool_executing" ? "正在调用工具"
                      : "正在思考…"
                    }</span>
                  </div>
                  {latestProgress && (
                    <div className="btc-progress">
                      <span className="btc-stage">{latestProgress.stage || latestProgress.step_id}</span>
                      {latestProgress.total > 0 && (
                        <span className="btc-pos">{latestProgress.completed}/{latestProgress.total}</span>
                      )}
                    </div>
                  )}
                </div>
                <ArtifactPreview toolMessages={toolMessages} compact onViewInSidebar={() => onOpenPreviewUrl && onOpenPreviewUrl("tab:artifacts")} />
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
          {!isEditing && !isThinkingInline && !isStreamingText && (
            <MessageActions
              message={m}
              isUser={isUser}
              cleanedText={typeof displayContent === "string" ? sanitizeMessageContent(displayContent) : ""}
              rawText={typeof displayContent === "string" ? displayContent : ""}
              assistant={assistant}
              alwaysShow={isLast && !loading}
              onRegenerate={!isUser ? onRegenerate : undefined}
              onRetry={isUser ? onRetry : undefined}
              onEdit={onEditMessage}
              onDelete={onDeleteMessage}
            />
          )}
          {isInner && onUpgradeToWorkbench && !isError && !isStreamingText && !isThinkingInline && !isEditing && (
            <button
              className="message-action-btn upgrade-standalone"
              onClick={() => onUpgradeToWorkbench(mctx.inner.id)}
              title="把这次子调用升级为独立工作台会话"
            >
              <Icon name="workflow" size={12} />
              <span>在 {mctx.inner.name} 工作台打开</span>
              <Icon name="external" size={11} />
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="vs-container">
      <Virtuoso
        ref={virtuosoRef}
        className="vs-content"
        data={rows}
        followOutput={(isAtBottom) => (isAtBottom ? "smooth" : false)}
        atBottomStateChange={setAtBottom}
        itemContent={(index) => (
          <div key={rowKey(rows[index], index)} data-row-index={index}>
            {renderRow(index)}
          </div>
        )}
        components={{
          Footer: () => (
            <>
              {uiBlocks.length > 0 && (
                <div className="ui-blocks-region" key="ui-blocks-region">
                  {uiBlocks.map((b) => (
                    <GeneratedComponent key={b.blockId} block={b} />
                  ))}
                </div>
              )}
              {approval && (
                <ApprovalBubble
                  approval={approval}
                  onRespond={onRespondApproval}
                  toolMessages={currentTurnToolMessages}
                />
              )}
            </>
          ),
        }}
      />

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
