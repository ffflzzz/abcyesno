import React, { useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo, forwardRef } from "react";
import Icon from "./Icon.jsx";
import { Virtuoso } from "react-virtuoso";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ThinkingIndicator from "./ThinkingIndicator.jsx";
import ArtifactPreview from "./ArtifactPreview.jsx";
import TerminalToolCard from "./TerminalToolCard.jsx";
import TypewriterText from "./TypewriterText.jsx";
import ApprovalBubble from "./ApprovalBubble.jsx";
import GeneratedComponent from "./GeneratedComponent.jsx";
import TableBlock from "./ui/TableBlock.jsx";
import MessageActions from "./MessageActions.jsx";
import ImageChip from "./ui/ImageChip.jsx";
import { useContractEvents } from "../hooks/useContractEvents.js";
import bachAvatar from "../assets/bach-avatar.png";

/**
 * Virtuoso `components.List` — the actual container that holds the rows.
 * With `customScrollParent`, the scrollable viewport is `.vs-container` (full
 * chat width, scrollbar flush against the window edge), but the *list itself*
 * is capped and centered to match the composer max-width.
 */
const CenteredList = forwardRef(function CenteredList(props, ref) {
  return <div ref={ref} {...props} className="vs-list" />;
});

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

// ── Extract data-URL images from markdown content ───────────────
// User messages embed screenshots as ![图片N](data:image/...;base64,...)
// which can be 2MB+. Passing these through ReactMarkdown on every stream
// token chokes the main thread (re-parse 2MB markdown → base64 decode →
// <img> layout) and inside react-virtuoso the <img> node gets repeatedly
// unmounted/remounted so it never finishes decoding.
//
// Solution: extract data-URL images BEFORE markdown rendering, render them
// as standalone <img> elements, and strip the ![](data:...) from text.
const DATA_URL_IMG_RE = /!\[([^\]]*)\]\((data:image\/[^)]+)\)/g;

/** @returns {{ images: Array<{src,alt}>, text: string }} */
function extractDataUrlImages(markdown) {
  if (typeof markdown !== "string" || !markdown.includes("data:image")) {
    return { images: [], text: markdown };
  }
  const images = [];
  const text = markdown.replace(DATA_URL_IMG_RE, (_match, alt, src) => {
    images.push({ src, alt: alt || "" });
    return ""; // remove the image inline syntax
  });
  return { images, text };
}

/** Memoized wrapper so identical content doesn't re-extract. */
const extractCache = new Map();
const EXTRACT_CACHE_MAX = 200;
function cachedExtract(markdown) {
  if (typeof markdown !== "string") return { images: [], text: markdown };
  const hit = extractCache.get(markdown);
  if (hit) return hit;
  const result = extractDataUrlImages(markdown);
  if (extractCache.size > EXTRACT_CACHE_MAX) extractCache.clear();
  extractCache.set(markdown, result);
  return result;
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

  // Phase 3.5: 清洗破损 markdown 表格残留的孤立竖线。
  // 模型（尤其 reasoning 模型）偶尔输出不合格表格：表头只有一个 `|`、
  // 分隔行不是 `|---|`、后续行无 `|`。ReactMarkdown 无法解析 → 退化为
  // 裸 "|xxx" 文本，极难看。GFM 表格允许省略首尾 `|`，因此只在「该行
  // 含 ≤1 个竖线」时剥离首尾孤立 `|`，对合法多列表格无任何影响。
  t = t.split("\n").map((line) => {
    const pipeCount = (line.match(/\|/g) || []).length;
    if (pipeCount <= 1) {
      return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "");
    }
    return line;
  }).join("\n");

  // Phase 4: Repair broken code fences so ReactMarkdown doesn't swallow
  // headings/lists/tables into a giant <pre> block.
  // Models often emit malformed markdown: an opening ```bash never gets closed,
  // or a reply restarts mid-stream producing nested/conflicting fences. We:
  //   1. Pair fences with a stack (a closing fence has no language tag,
  //      same marker, and length >= the opening fence).
  //   2. Remove fence pairs whose inner content is mostly markdown prose
  //      (headings, lists, tables, bold) — those are bogus code blocks.
  //   3. Remove unclosed opening fences (their content becomes normal prose).
  //   4. Keep legitimate code blocks untouched.
  t = repairFences(t);

  t = t.split("\n").map((l) => l.trimEnd()).join("\n").trim();

  return t;
}

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

/**
 * Some providers / reasoning models return reasoning_content that is identical
 * to the final assistant content. We suppress the "深度推理" block only when the
 * reasoning is EXACTLY equal to the answer — NOT when it's merely contained in
 * it. Reasoning models routinely restate part of the answer inside their
 * thinking; hiding every such case made the thinking block disappear entirely
 * (the user reported "看不到模型的thinking 过程"). See #thinking-visible.
 */
function formatSilentMs(ms) {
  if (!(ms > 0)) return "0 秒";
  if (ms >= 60000) return `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`;
  return `${Math.round(ms / 1000)} 秒`;
}

function isDuplicateReasoning(reasoning, content) {
  const r = String(reasoning || "").replace(/\s+/g, " ").trim();
  const c = String(content || "").replace(/\s+/g, " ").trim();
  if (!r) return true;
  if (!c) return false;
  if (r === c) return true;
  return false;
}

/**
 * ClarifyQuestionRow — renders the clarify tool's interactive question UI.
 *
 * When the agent calls `clarify(question, choices)` the gateway blocks on a
 * user response.  In CLI this is a rich select widget; in our Electron app
 * we render an inline question card with choice buttons + free-text input.
 *
 * The user's answer is sent as a normal chat message — the gateway's
 * _maybe_intercept_clarify_text (run.py:8705) intercepts it and routes it
 * to the waiting clarify resolver, unblocking the agent.
 */
function ClarifyQuestionRow({ toolMsg, assistantAvatar, onSend }) {
  const [reply, setReply] = useState("");
  const [submitted, setSubmitted] = useState(false);

  // Parse question + choices from tool args
  let question = "";
  let choices = null;
  try {
    const args = typeof toolMsg.args === "string" ? JSON.parse(toolMsg.args) : (toolMsg.args || {});
    // Handle both raw clarify schema {question, choices} and wrapped
    // LangGraph format {context: "Asking ...", ...}
    question = args.question || args.context || "";
    if (typeof question === "string" && question.startsWith("Asking ")) {
      question = question.replace(/^Asking\s+/, "");
    }
    choices = Array.isArray(args.choices) ? args.choices : null;
  } catch {
    question = String(toolMsg.args || "");
    choices = null;
  }

  const handleChoice = (text) => {
    if (submitted || !onSend) return;
    setSubmitted(true);
    onSend(text);
  };

  const handleSubmit = () => {
    const text = reply.trim();
    if (!text || submitted || !onSend) return;
    setSubmitted(true);
    onSend(text);
  };

  const status = mapStatus(toolMsg.status);
  const isDone = status === "complete" || status === "error";

  return (
    <div className="message-row assistant">
      <div className="message-avatar agent-avatar">{assistantAvatar}</div>
      <div className="message-col">
        <div className="clarify-bubble">
          <div className="clarify-header">
            <span className="clarify-icon">❓</span>
            <span className="clarify-label">需要确认</span>
            {!isDone && (
              <span className="clarify-status-hint">请选择或输入回答</span>
            )}
            {isDone && (
              <span className="clarify-status-done">已结束</span>
            )}
          </div>
          <div className="clarify-question">{question}</div>

          {!isDone && choices && choices.length > 0 && (
            <div className="clarify-choices">
              {choices.map((c, i) => (
                <button
                  key={`cq-${i}`}
                  className="clarify-choice-btn"
                  onClick={() => handleChoice(c)}
                >
                  {c}
                </button>
              ))}
              <button
                className="clarify-choice-btn clarify-other"
                onClick={() => {
                  const el = document.querySelector(".clarify-freetext-input");
                  if (el) el.focus();
                }}
              >
                其他（自定义）
              </button>
            </div>
          )}

          {!isDone && (
            <div className="clarify-freetext">
              <input
                className="clarify-freetext-input"
                type="text"
                placeholder={choices?.length ? "或直接输入你的答案…" : "输入你的回答…"}
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
                disabled={submitted}
              />
              <button
                className="clarify-send-btn"
                onClick={handleSubmit}
                disabled={submitted || !reply.trim()}
              >
                发送
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
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

const MarkdownView = React.memo(function MarkdownView({ cleaned, onImageClick, onOpenPreviewUrl }) {
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
              style={{ maxWidth: "100%", maxHeight: "60vh", cursor: "pointer", borderRadius: "6px" }}
              onClick={() => onImageClick && onImageClick(props.src, props.alt)}
              alt={props.alt || ""}
            />
          ),
          // Intercept external links so they open in the built-in browser
          // (ResultPanel webview via onOpenPreviewUrl) instead of navigating
          // the entire app BrowserWindow, which would crash / replace the UI.
          // Non-http(s) protocols (mailto, file, etc.) fall through to default.
          a: ({ node, href, children, ...rest }) => (
            <a
              href={href}
              onClick={(e) => {
                if (href && /^https?:/i.test(href) && onOpenPreviewUrl) {
                  e.preventDefault();
                  onOpenPreviewUrl(href);
                }
              }}
              {...rest}
            >
              {children}
            </a>
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
});

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

/** Count lines that look like markdown prose (headings, lists, tables, bold, numbered lists).
 *  Single-level `#` is excluded because it is also common in shell comments inside code blocks. */
function countMdStructureLines(lines) {
  return lines.filter((l) =>
    /^#{2,6}\s/.test(l) ||
    /^[-*+]\s+/.test(l) ||
    /^\|[^|]+\|[^|]+\|/.test(l) ||
    /\*\*[^*]+\*\*/.test(l) ||
    /^\d+\.\s/.test(l)
  ).length;
}

/**
 * Repair broken markdown code fences.
 * - Pairs opening/closing fences with a stack.
 * - Removes fence pairs whose inner content is mostly markdown structure
 *   (those are bogus code blocks that would swallow headings/lists).
 * - Removes unclosed opening fences so the rest of the message renders normally.
 * - Leaves legitimate code blocks untouched.
 */
function repairFences(text) {
  const lines = text.split("\n");
  const fenceRe = /^(\`{3,}|~{3,})(\w*)\s*$/;
  const fenceLines = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(fenceRe);
    if (m) fenceLines.push({ idx: i, marker: m[1][0], len: m[1].length, lang: m[2] });
  }
  if (fenceLines.length === 0) return text;

  const stack = [];
  const pairs = [];
  const unclosed = [];
  for (const f of fenceLines) {
    if (stack.length) {
      const top = stack[stack.length - 1];
      // A closing fence has no language tag, matches the opener marker, and is at least as long.
      if (!f.lang && f.marker === top.marker && f.len >= top.len) {
        pairs.push({ open: top, close: f });
        stack.pop();
        continue;
      }
    }
    stack.push(f);
  }
  while (stack.length) unclosed.push(stack.pop());

  const removeIdx = new Set();
  for (const p of pairs) {
    const span = lines.slice(p.open.idx + 1, p.close.idx);
    const nonEmpty = span.filter((l) => l.trim()).length;
    const md = countMdStructureLines(span);
    // If >25% of the block looks like markdown prose, it is a bogus fence pair.
    if (nonEmpty > 0 && md / nonEmpty > 0.25) {
      removeIdx.add(p.open.idx);
      removeIdx.add(p.close.idx);
    }
  }
  // Drop orphan opening fences; their following content should render as normal prose.
  for (const f of unclosed) removeIdx.add(f.idx);

  if (removeIdx.size === 0) return text;
  return lines.map((l, i) => (removeIdx.has(i) ? "" : l)).join("\n");
}

// ── ASCII / box-drawing art detection ──────────────────────
// Matches lines heavy in box-drawing Unicode chars (flowcharts, trees, diagrams).
// A block is "ASCII art" when ≥3 consecutive lines each contain ≥2 box-drawing chars.
const BOX_DRAWING_RE = /[┌┐└┘│─├┤┬┴┼╭╮╯╰═║╞╟╠╚╔╗╝▓░▀▄■□▲▼◆◇●○·━┿╼╾╶╷╺╻╱╲╳╴╵╶╷╸╹]/;
const ASCII_ART_MIN_LINES = 3;
const ASCII_ART_MIN_CHARS_PER_LINE = 2;

function detectAsciiArtBlocks(text) {
  if (!text) return [];
  const lines = text.split("\n");
  const blocks = []; // [{start, end, content}]
  let blockStart = -1;
  for (let i = 0; i < lines.length; i++) {
    const hasBox = (lines[i].match(BOX_DRAWING_RE) || []).length >= ASCII_ART_MIN_CHARS_PER_LINE;
    if (hasBox && blockStart < 0) {
      blockStart = i;
    } else if (!hasBox && blockStart >= 0) {
      if (i - blockStart >= ASCII_ART_MIN_LINES) {
        blocks.push({ start: blockStart, end: i, content: lines.slice(blockStart, i).join("\n") });
      }
      blockStart = -1;
    }
  }
  // Handle block extending to end of text
  if (blockStart >= 0 && lines.length - blockStart >= ASCII_ART_MIN_LINES) {
    blocks.push({ start: blockStart, end: lines.length, content: lines.slice(blockStart).join("\n") });
  }
  return blocks;
}

// Render markdown with ASCII art blocks (box-drawing diagrams) collapsed into
// individually toggleable sections. Normal text renders via MarkdownView;
// detected ASCII art regions get wrapped in <pre> with max-height + scroll.
function AsciiArtAwareMarkdown({ cleaned, onImageClick, onOpenPreviewUrl, asciiBlocks }) {
  const [expandedBlocks, setExpandedBlocks] = useState({});

  if (!asciiBlocks || asciiBlocks.length === 0) {
    return <MarkdownView cleaned={cleaned} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} />;
  }

  // Split content into segments: normal text and ASCII art blocks
  const segments = [];
  let lastEnd = 0;
  const lines = cleaned.split("\n");
  for (const block of asciiBlocks) {
    if (block.start > lastEnd) {
      segments.push({ type: "text", content: lines.slice(lastEnd, block.start).join("\n") });
    }
    segments.push({ type: "ascii", content: block.content, lineCount: block.end - block.start });
    lastEnd = block.end;
  }
  if (lastEnd < lines.length) {
    segments.push({ type: "text", content: lines.slice(lastEnd).join("\n") });
  }

  const toggleBlock = (idx) => {
    setExpandedBlocks(prev => ({ ...prev, [idx]: !prev[idx] }));
  };

  return (
    <>
      {segments.map((seg, idx) =>
        seg.type === "ascii" ? (
          <div key={`ascii-${idx}`} className="ascii-art-block">
            <button
              className="ascii-art-toggle"
              onClick={() => toggleBlock(idx)}
              title={expandedBlocks[idx] ? "收起图表" : "展开图表"}
            >
              <Icon name="chevron" size={10} style={{ transform: expandedBlocks[idx] ? "rotate(-90deg)" : "rotate(90deg)", transition: "transform 0.15s" }} />
              <span className="ascii-art-label">流程图 / 图表（{seg.content.split("\n").length} 行）</span>
            </button>
            {expandedBlocks[idx] && (
              <pre className="ascii-art-pre">{seg.content}</pre>
            )}
          </div>
        ) : (
          <MarkdownView key={`text-${idx}`} cleaned={seg.content} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} />
        )
      )}
    </>
  );
}

// Collapse long assistant replies to a short preview with an expand toggle,
// so a big multi-section result doesn't flood the thread (简洁 + 可展开).
// ASCII art blocks are always independently collapsed regardless of total length.
const COLLAPSE_CHAR_THRESHOLD = 2000;
const COLLAPSE_LINE_THRESHOLD = 30;

const CollapsibleMarkdown = React.memo(function CollapsibleMarkdown({ cleaned, onImageClick, onOpenPreviewUrl }) {
  const [expanded, setExpanded] = useState(false);
  const asciiBlocks = useMemo(() => detectAsciiArtBlocks(cleaned), [cleaned]);
  const hasAsciiArt = asciiBlocks.length > 0;
  const lines = cleaned.split("\n");
  const tooLong = cleaned.length > COLLAPSE_CHAR_THRESHOLD || lines.length > COLLAPSE_LINE_THRESHOLD;

  if (!tooLong && !hasAsciiArt) {
    return <MarkdownView cleaned={cleaned} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} />;
  }

  // ── Build preview: skip ASCII art lines so the user sees useful text ──
  const nonAsciiLineIndices = [];
  for (let i = 0; i < lines.length; i++) {
    const inAscii = asciiBlocks.some(b => i >= b.start && i < b.end);
    if (!inAscii) nonAsciiLineIndices.push(i);
  }
  const previewLines = nonAsciiLineIndices.slice(0, 15).map(i => lines[i]);
  const previewText = previewLines.join("\n");
  const needsCollapse = tooLong || hasAsciiArt;

  if (!needsCollapse) {
    return <MarkdownView cleaned={cleaned} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} />;
  }

  return (
    <div className={`collapsible-md ${expanded ? "expanded" : "collapsed"}`}>
      <div className="collapsible-md-content">
        {expanded ? (
          <AsciiArtAwareMarkdown cleaned={cleaned} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} asciiBlocks={asciiBlocks} />
        ) : (
          <MarkdownView cleaned={previewText || cleaned.slice(0, 200)} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} />
        )}
      </div>
      {!expanded && <div className="collapsible-md-fade" />}
      <button className="collapsible-md-toggle" onClick={() => setExpanded(!expanded)}>
        {expanded ? (<><Icon name="chevron" size={12} style={{ transform: "rotate(-90deg)" }} /> 收起</>) : (
          <><Icon name="chevron" size={12} style={{ transform: "rotate(90deg)" }} /> 展开全部（{lines.length} 行{hasAsciiArt ? `，含 ${asciiBlocks.length} 处图表` : ""}）</>
        )}
      </button>
    </div>
  );
});

function formatContent(content, onImageClick, onOpenPreviewUrl) {
  const cleaned = sanitizeMessageContent(content);
  if (typeof cleaned !== "string") {
    return <pre className="json-block">{JSON.stringify(content, null, 2)}</pre>;
  }
  if (!cleaned) return null;
  return <CollapsibleMarkdown cleaned={cleaned} onImageClick={onImageClick} onOpenPreviewUrl={onOpenPreviewUrl} />;
}

function mapStatus(s) {
  if (s === "running" || s === "in_progress") return "running";
  if (s === "error" || s === "failed") return "error";
  if (s === "interrupted" || s === "cancelled" || s === "canceled") return "interrupted";
  if (s === "pending") return "pending";
  return "complete";
}

// Kawaii spinner phrases emitted by conversation_loop.py (e.g. "◉_◉ computing...").
// These are transient CLI-style status updates and should be shown inline with
// the main spinner instead of as a separate block.
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

/**
 * ThinkingTranscript — shows the model's real reasoning tokens (thinkingText)
 * streamed from the backend. Renders as inline streaming text (no card/bubble).
 * Default expanded so reasoning is visible during thinking AND during/after
 * the text-generation phase (click to collapse when you don't want it taking
 * vertical space).
 */
function ThinkingTranscript({ text, collapsed = false }) {
  const ref = useRef(null);
  const pinnedRef = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinnedRef.current && !collapsed) el.scrollTop = el.scrollHeight;
  }, [text, collapsed]);

  if (!text || !text.trim()) return null;

  return (
    <div
      className={`thinking-inline ${collapsed ? "collapsed" : ""}`}
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
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
  // 运行期拆分行：推理与正文同源一条消息，key 必须区分（2026-08-27）
  if (row.type === "reasoning-only") return `reasoning-${row.data?.id || index}`;
  if (row.type === "text-only") return `text-${row.data?.id || index}`;
  if (row.type === "console") return "agent-console";
  return "thinking";
}

/**
 * Estimated height per row type (spec §5.1)
 */
function estimatedHeight(row) {
  if (row.type === "tools") return 120;
  if (row.type === "thinking") return 48;
  if (row.type === "reasoning-only") return 140;
  if (row.type === "text-only") return 100;
  if (row.type === "console") return 320;
  return 80; // message
}

// ProgressLine — WorkBuddy / Codex style blockquote for a single tool call.
// Replaces the old ToolCard list inside ToolsRow's expanded view: each tool
// becomes one inline line with a status marker, the tool name, the args
// preview, and a short result summary — readable as plain text rather than
// a structured timeline card.
// 参数预览去噪（2026-08-27）：executor 传来的 args 含 tool_id/name 等内部
// 字段，原样打印又长又乱。解析 JSON 后跳过内部键，输出紧凑的 key=value；
// 非 JSON 则回退到截断的原文。
function friendlyArgsPreview(raw) {
  const args = String(raw || "").replace(/\n+/g, " ").trim();
  try {
    const obj = JSON.parse(args);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      const skip = new Set(["tool_id", "name", "type"]);
      const parts = [];
      for (const [k, v] of Object.entries(obj)) {
        if (skip.has(k)) continue;
        let val = typeof v === "string" ? v.replace(/\n+/g, " ") : JSON.stringify(v);
        if (val.length > 120) val = val.slice(0, 120) + "…";
        parts.push(`${k}=${val}`);
      }
      if (parts.length > 0) {
        const joined = parts.join("  ");
        return joined.length > 200 ? joined.slice(0, 200) + "…" : joined;
      }
    }
  } catch { /* 非 JSON，走原文截断 */ }
  return args.length > 160 ? args.slice(0, 160) + "…" : args;
}

function ProgressLine({ m, status }) {
  const marker =
    status === "running" ? "…" :
    status === "error" || status === "interrupted" ? "✗" :
    "✓";
  const args = (m.args || "").replace(/\n+/g, " ").trim();
  const argsPreview = friendlyArgsPreview(args);
  const hasResult = m.result !== undefined && m.result !== null;
  const resultText = hasResult
    ? (typeof m.result === "string" ? m.result : JSON.stringify(m.result)).replace(/\n+/g, " ").trim()
    : "";
  const resultPreview = resultText.length > 200 ? resultText.slice(0, 200) + "…" : resultText;
  return (
    <div className={`progress-line progress-${status}`}>
      <span className="progress-marker">{marker}</span>
      <span className="progress-text">
        <span className="progress-tool">{m.toolName || "tool"}</span>
        {argsPreview && <code className="progress-args">{argsPreview}</code>}
        {m.durationMs !== undefined && m.durationMs !== null && (
          <span className="progress-meta">{m.durationMs}ms</span>
        )}
        {resultPreview && (
          <div className="progress-result">{resultPreview}</div>
        )}
      </span>
    </div>
  );
}

/**
 * AgentProcessStream — 运行期逐行打印的 agent 过程流（2026-08-27）。
 * 推理（暗色斜体）/ 工具（✓✗行）/ 回复（正文）按实际发生顺序逐行打入
 * 同一块固定高度滚动区；自动滚到底，用户上滚即暂停。回合结束后该块
 * 整体消失，由折叠的推理条 + 工具条 + 完整回复气泡接管。
 */
/**
 * ConsoleToolSegment — 过程流里的工具段：保留「汇总条 + 手动收纳」交互。
 * 头部：⚙ N 个工具调用 · 名称×次数 · 状态徽标 · 折叠箭头；
 * 体部：固定高度 progress-stream 流式滚动（自动滚底、上滚暂停）。
 */
function ConsoleToolSegment({ seg, messages }) {
  const byId = useMemo(() => new Map((messages || []).map((m) => [m.id, m])), [messages]);
  const items = (seg.ids || []).map((id) => byId.get(id)).filter(Boolean);
  const hasRunning = items.some((m) => m.status === "running" || m.status === "in_progress");
  // 2026-08-28：段内工具全部完成 → 延迟 2s 自动收纳（只展开当前活跃段）。
  // 立即收纳会让"展开→完成→收起"高频切换产生闪烁感，2s 缓冲让用户看清
  // 完成状态；手动点头部切换后尊重用户选择，直到下一个运行/完成边界。
  const [expandOverride, setExpandOverride] = useState(null);
  const [autoClosed, setAutoClosed] = useState(!hasRunning);
  useEffect(() => {
    if (hasRunning) {
      // 新运行边界：重置手动选择，立即展开
      setExpandOverride(null);
      setAutoClosed(false);
      return;
    }
    // 完成 → 2s 后收纳
    const t = setTimeout(() => setAutoClosed(true), 2000);
    return () => clearTimeout(t);
  }, [hasRunning]);
  const open = expandOverride !== null ? expandOverride : !autoClosed;
  const toggleOpen = () => setExpandOverride(!open);
  const streamRef = useRef(null);
  const userScrolledRef = useRef(false);
  useEffect(() => {
    const el = streamRef.current;
    if (!el || !open) return;
    if (!userScrolledRef.current) el.scrollTop = el.scrollHeight;
  });
  const handleScroll = useCallback(() => {
    const el = streamRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    userScrolledRef.current = !nearBottom;
  }, []);
  const groups = {};
  for (const t of items) {
    const name = t.toolName || "tool";
    (groups[name] = groups[name] || []).push(t);
  }
  const toolNames = Object.keys(groups);
  return (
    <div className="agent-console-tools">
      <div className="agent-console-tools-head" onClick={toggleOpen}>
        <span className="act-tools-icon"><Icon name={hasRunning ? "settings" : "check-circle"} size={12} /></span>
        <span className="act-tools-count">{items.length} 个工具调用</span>
        <span className="act-tools-names">
          {toolNames.map((n) => `${n}×${groups[n].length}`).join(" · ")}
        </span>
        <span className={`act-tools-status ${hasRunning ? "running" : "complete"}`}>
          {hasRunning ? "执行中…" : "全部完成"}
        </span>
        <span className={`act-tools-chevron ${open ? "expanded" : ""}`}>
          <Icon name="chevron" size={11} style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }} />
        </span>
      </div>
      {open && (
        <div className="progress-stream" ref={streamRef} onScroll={handleScroll}>
          {items.map((m) => {
            const st = m.status === "running" || m.status === "in_progress" ? "running"
              : m.isError ? "error" : "complete";
            return <ProgressLine key={m.id} m={m} status={st} />;
          })}
        </div>
      )}
    </div>
  );
}

function AgentProcessStream({ timeline, messages }) {
  const ref = useRef(null);
  const userScrolledRef = useRef(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!userScrolledRef.current) el.scrollTop = el.scrollHeight;
  });
  const handleScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    userScrolledRef.current = !nearBottom;
  }, []);
  const byId = useMemo(() => new Map((messages || []).map((m) => [m.id, m])), [messages]);
  return (
    <div className="agent-console" ref={ref} onScroll={handleScroll}>
      {timeline.map((seg, i) => {
        if (seg.kind === "reasoning") {
          // 2026-08-28：推理段渲染为正式的 thinking 框（ReasoningBlock）——
          // 回答前就弹出并流式展开，充分展示思考过程；运行结束随整流收纳。
          // 此前用暗色斜体小字，用户感知不到"思考框已弹出"。
          return seg.text && seg.text.trim() ? (
            <ReasoningBlock key={`r-${i}`} text={seg.text} streaming={loading} />
          ) : null;
        }
        if (seg.kind === "tools") {
          return <ConsoleToolSegment key={`t-${i}`} seg={seg} messages={messages} />;
        }
        return seg.text && seg.text.trim() ? (
          <div key={`x-${i}`} className="agent-console-text">{seg.text}</div>
        ) : null;
      })}
      <div className="agent-console-cursor" aria-hidden="true" />
    </div>
  );
}

function ToolsRow({ items, assistantAvatar, inCurrentTurn = false, toolStatus = {}, onImageClick, onViewInSidebar }) {
  // 「当前回合内」= loading 且本行位于最后一条 assistant 消息之后（后面只有
  // thinking 行也不算结束）。不能用 isLastRow —— 工具全部完成后 thinking 行
  // 会顶上来把 last 位抢走，导致回合还没结束就提前收纳（2026-08-27）。
  const toolsRunning = inCurrentTurn;
  const hasRunning = items.some(m => mapStatus(m.status) === "running");
  const allComplete = items.every(m => {
    const s = mapStatus(m.status);
    // interrupted / complete / error 都视为“已收尾”，不再显示“执行中…”
    return s !== "running" && s !== "in_progress";
  });
  const interruptedCount = items.filter(m => mapStatus(m.status) === "interrupted").length;
  const totalTools = items.length;

  // 2026-08-27 交互约定：运行中自动展开（用户实时看工具明细），回合结束
  // 自动收纳。手动点按头切换后尊重用户选择，直到下一个运行/结束边界
  // （override 重置为 null，重新跟随自动行为）。
  // 回合进行中就保持展开（即使本行工具已全部完成——回合结束由 loading 收场）
  const phaseRunning = toolsRunning;
  const prevRunningRef = useRef(phaseRunning);
  const [expandOverride, setExpandOverride] = useState(null); // true=开 false=收 null=跟随自动
  useEffect(() => {
    if (prevRunningRef.current !== phaseRunning) {
      prevRunningRef.current = phaseRunning;
      setExpandOverride(null);
    }
  }, [phaseRunning]);
  const expanded = expandOverride !== null ? expandOverride : phaseRunning;
  const toggle = () => setExpandOverride(!expanded);

  // 固定高度块内流式打印：默认自动滚到底；用户上滚即暂停，滚回底部恢复。
  const streamRef = useRef(null);
  const streamUserScrolledRef = useRef(false);
  useEffect(() => {
    if (prevRunningRef.current !== phaseRunning) {
      streamUserScrolledRef.current = false; // 运行边界重置滚动跟随
    }
    const el = streamRef.current;
    if (!el || !expanded) return;
    if (!streamUserScrolledRef.current) el.scrollTop = el.scrollHeight;
  }, [expanded, items, phaseRunning]);
  const handleStreamScroll = useCallback(() => {
    const el = streamRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    streamUserScrolledRef.current = !nearBottom;
  }, []);

  const groups = {};
  for (const t of items) {
    const name = t.toolName || "tool";
    if (!groups[name]) groups[name] = [];
    groups[name].push(t);
  }
  const toolNames = Object.keys(groups);

  return (
    <div className="message-row assistant tool-row">
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
            className={`tool-summary-status ${allComplete ? (interruptedCount ? "interrupted" : "complete") : "running"}`}
            style={!allComplete ? {
              color: "#d29922",
              animation: "status-running-pulse 1.5s ease-in-out infinite",
            } : undefined}
          >
            {!allComplete
              ? "执行中…"
              : interruptedCount
                ? (interruptedCount === totalTools ? "已中断" : `已中断 (${interruptedCount}/${totalTools})`)
                : "全部完成"}
          </span>
          <span className={`tool-summary-chevron ${expanded ? "expanded" : ""}`}><Icon name="chevron" size={12} style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }} /></span>
        </div>
        {expanded && (
          <div className="progress-stream" ref={streamRef} onScroll={handleStreamScroll}>
              {items.map((m, i) => {
                const mStatus = mapStatus(m.status);
                // A terminal tool that launched a background PTY session keeps
                // its live interactive xterm pane; the inline blockquote-style
                // progress line below it shows the call/result summary.
                if ((m.toolName === "terminal" || m.toolName === "terminal_tool") && m.processId) {
                  return (
                    <div key={m.id || `term-${i}`}>
                      <TerminalToolCard
                        toolName={m.toolName || "terminal"}
                        status={mStatus}
                        result={m.result !== undefined && m.result !== null ? m.result : m.content}
                        durationMs={m.durationMs}
                        terminalChunks={m.terminalChunks || []}
                        processId={m.processId}
                        interactive
                        terminalClosed={!!m.terminalClosed}
                        defaultExpanded={mStatus === "running" || mStatus === "in_progress"}
                      />
                      <ProgressLine m={m} status={mStatus} />
                    </div>
                  );
                }
                return <ProgressLine key={m.id || `line-${i}`} m={m} status={mStatus} />;
              })}
          </div>
        )}
        <ArtifactPreview toolMessages={items} compact onViewInSidebar={onViewInSidebar} />
      </div>
    </div>
  );
}

function MessageThread({ messages = [], loading, streamPhase, thinkingText, reasoningText = "", backendSilentMs = 0, turnElapsedMs = 0, timeline = null, uiBlocks = [], stalled = false, subagents = [], moaRefs = [], moaAggregating = null, toolStatus = {}, reviewSummary = null, onRetry, onRegenerate, assistant, manifests = [], onOpenPreviewUrl, approval, onRespondApproval, sessionId, onEditMessage, onDeleteMessage, editingMessageId, onSaveEdit, onCancelEdit, onSend }) {
  const [lightbox, setLightbox] = useState(null);
  // Stable image-click handler so memoized markdown bubbles (React.memo on
  // CollapsibleMarkdown/MarkdownView) don't re-render on every stream token.
  const handleImageClick = useCallback((src, alt) => setLightbox({ src, alt }), []);

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

  // ── Workflow progress: extract stage list from contract events ──
  const contractEvents = useContractEvents(sessionId);
  const { latestProgress, progressStages } = useMemo(() => {
    if (!contractEvents || !contractEvents.length) return { latestProgress: null, progressStages: [] };
    // Collect unique stages in order, keeping the last status per step_id
    const stageMap = new Map(); // step_id -> payload
    let latest = null;
    for (const ev of contractEvents) {
      if (ev.type === "workflow.progress" && ev.payload) {
        stageMap.set(ev.payload.step_id, ev.payload);
        if (!latest || ev.payload.status === "running") latest = ev.payload;
      }
    }
    // Fallback: if no running, use last event
    if (!latest && stageMap.size > 0) {
      const vals = [...stageMap.values()];
      latest = vals[vals.length - 1];
    }
    return { latestProgress: latest, progressStages: [...stageMap.values()] };
  }, [contractEvents]);

  // @ mention protocol (spec §2): a user message beginning with `@<name>`
  // is a sub-call into a workflow. We render it as an inner block and, for the
  // assistant reply that follows, offer an "open in workbench" upgrade (decision ①).
  function parseMentionToken(text) {
    const m = String(text || "").match(/^\s*@([^\s@]+)/);
    if (!m) return null;
    const token = m[1];
    const norm = (s) => s.toLowerCase().replace(/[-_]+/g, "_");
    const tokenNorm = norm(token);
    const hit = manifests.find(
      (x) =>
        norm(x.name) === tokenNorm ||
        norm(x.id) === tokenNorm ||
        x.name === token ||
        x.id === token
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

  // ── Memoized rows ──────────────────────────────────────────────
  // Without useMemo, rows gets a new array reference on every parent
  // re-render (i.e. every stream token).  Virtuoso then assumes the
  // entire data set changed and re-renders all visible items, causing
  // flicker even for unchanged message bubbles.
  const rows = useMemo(() => {
    // ── agent 时间线（2026-08-27 v2）──
    // 运行中：整个回合渲染为一条「逐行打印」的过程流（AgentProcessStream）——
    // 推理 / 工具 / 回复按实际发生顺序逐行打入同一块固定高度滚动区，
    // 不再按段拆气泡（避免回复半句成块、完成调用不收纳、thinking 消失）。
    // 回合结束：整块收纳，回退到合并渲染（折叠的推理条 + 工具条 + 完整回复气泡）。
    if (timeline && timeline.length > 0 && loading) {
      let lastUserIdx = -1;
      messages.forEach((m, i) => { if (m && m.role === "user") lastUserIdx = i; });
      if (lastUserIdx >= 0) {
        const r = renderGrouped(messages.slice(0, lastUserIdx + 1));
        r.push({ type: "console", timeline });
        return r;
      }
    }

    const grouped = renderGrouped(messages);
    const r = [...grouped];
    // Append thinking / progress indicator when agent is busy.
    if (loading) {
      const last = r[r.length - 1];
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
          r.push({ type: "thinking", phase: streamPhase });
        }
      } else if (lastHasContent) {
        r.push({ type: "thinking", phase: streamPhase || "tool_executing" });
      }
    }

    // 2026-08-27「过程展示、结束收纳」补全：回合进行中，若 assistant 消息
    // （含已流出的正文）后面还跟着工具行，把该消息拆成两行——
    //   推理行留在原位（时序：先思考），正文行追加到最后（时序：后回答）。
    // 否则回复文本夹在 thinking 与 tool call 之间，阅读顺序混乱。
    // 回合结束（loading=false）自动还原为单条完整消息。
    if (loading) {
      let lastMsgIdx = -1;
      r.forEach((row, i) => {
        if (row.type === "message" && row.data && row.data.role === "assistant") lastMsgIdx = i;
      });
      const toolsAfter = lastMsgIdx >= 0 && r.some((row, i) => i > lastMsgIdx && row.type === "tools");
      if (toolsAfter) {
        const msgRow = r[lastMsgIdx];
        const hasText = String(sanitizeMessageContent(msgRow.data.content) || "").trim().length > 0;
        if (hasText) {
          r.splice(lastMsgIdx, 1);
          r.splice(lastMsgIdx, 0, { type: "reasoning-only", data: msgRow.data });
          r.push({ type: "text-only", data: msgRow.data, _splitText: true });
        }
      }
    }

    return r;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, loading, streamPhase]);

  // 当前回合仍在写的那条 assistant 消息 id：loading 期间从后往前找的最后一条
  // assistant 消息行。它后面的 tools/thinking 行不代表回合结束 —— 推理块
  // 不能用 isLast（会被工具行挤掉 last 位）判定流式状态，否则工具一启动
  // 推理框就提前折叠（2026-08-27「过程展示、结束收纳」约定）。
  let currentAssistantMsgId = null;
  let lastAssistantRowIdx = -1;
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r.type === "message" && r.data && r.data.role === "assistant") {
      currentAssistantMsgId = r.data.id;
      lastAssistantRowIdx = i;
      break;
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
  // Use the outer .vs-container as the single scroll parent so the vertical
  // scrollbar sits flush against the window edge (no padding gutter) and we
  // avoid nested scrollers that caused horizontal scrollbar + resize freezes.
  const [scrollParent, setScrollParent] = useState(null);

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

  // Stable item-content callback for Virtuoso.  Without this, the inline
  // arrow created a new reference on every parent re-render, defeating
  // Virtuoso's internal item-level memoization and causing every visible
  // row to re-render (and re-run ReactMarkdown) on each stream token.
  const itemContent = useCallback(
    (index) => (
      <div key={rowKey(rows[index], index)} data-row-index={index}>
        {renderRow(index)}
      </div>
    ),
    // renderRow closes over the latest props/state; rows changes when
    // messages change (useMemo above).  Omitting renderRow from deps is
    // safe because it is a pure function of (index, rows, props) — when
    // those change the component re-renders and creates a new closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows]
  );

  const computeItemKey = useCallback(
    (index) => rowKey(rows[index], index),
    [rows]
  );

  const renderRow = (index) => {
    const row = rows[index];
    const isLastRow = index === rows.length - 1;

    if (row.type === "thinking") {
      // Dedicated thinking row: compact indicator only. Detailed progress lives
      // in the header status bar and right-panel workflow timeline — NOT in chat bubbles.
      //
      // When this row immediately follows a tools row, suppress the duplicate
      // avatar and indent to align with the content column above.  Otherwise
      // three identical avatars stack vertically (user → tools → thinking)
      // which looks cramped and redundant.
      const prevRow = index > 0 ? rows[index - 1] : null;
      const isContinuation = prevRow?.type === "tools";
      const spinnerText = isKawaiiSpinnerPhrase(thinkingText) ? thinkingText.trim() : "";
      const phaseLabel =
        !spinnerText
          ? !thinkingText
            ? row.phase === "text_generating" ? "正在生成回复"
            : row.phase === "tool_executing" ? "正在调用工具"
            : "正在思考…"
          : ""
          : "";
      const showStatusLine = spinnerText || phaseLabel;
      return (
        <div className={`message-row assistant${isContinuation ? " continuation" : ""}`}>
          {!isContinuation && <div className="message-avatar agent-avatar thinking">{assistantAvatar}</div>}
          <div className="message-col">
            <div className="assistant-body">
              <div className="bubble-thinking-compact">
                {showStatusLine && (
                  <div className="btc-line">
                    <span className="btc-spinner" />
                    <span className="btc-text">{spinnerText || phaseLabel}</span>
                  </div>
                )}
                {backendSilentMs > 45000 && (
                  <div className="btc-health">
                    ⏳ 后端已静默 {formatSilentMs(backendSilentMs)} —— 通常是长命令执行或模型思考中（SSE 连接正常）
                  </div>
                )}
                {progressStages.length > 0 && (
                  <div className="btc-workflow-progress">
                    <div className="btc-stage-bar">
                      {progressStages.map((s) => (
                        <span
                          key={s.step_id}
                          className={`btc-stage-chip ${s.status === "running" ? "active" : s.status === "done" ? "done" : ""}`}
                          title={s.message || s.step_id}
                        >
                          {s.stage || s.step_id}
                        </span>
                      ))}
                    </div>
                    {latestProgress?.total > 1 && (
                      <div className="btc-progress-track">
                        <div
                          className="btc-progress-fill"
                          style={{ width: `${(latestProgress.completed / latestProgress.total) * 100}%` }}
                        />
                      </div>
                    )}
                    {latestProgress?.message && (
                      <div className="btc-step-msg">{latestProgress.message}</div>
                    )}
                  </div>
                )}
                {!spinnerText && (
                  <ThinkingTranscript
                    text={thinkingText}
                    collapsed={!isLastRow}
                  />
                )}
                {stalled && (
                  <div className="btc-stall-warning">
                    <span className="btc-stall-icon">⚠</span>
                    <span>响应超时，后端可能未就绪。可点击停止后重试。</span>
                  </div>
                )}
              </div>
              {/* ArtifactPreview 已由 ToolsRow 渲染（message-thread 内唯一出口）：
                  这里再渲染一份会造成运行期「产物 chip 重复出现两次」。 */}
            </div>
          </div>
        </div>
      );
    }

    if (row.type === "console") {
      return (
        <div className="message-row assistant">
          <div className="message-avatar agent-avatar thinking">{assistantAvatar}</div>
          <div className="message-col">
            <AgentProcessStream timeline={row.timeline} messages={messages} />
          </div>
        </div>
      );
    }

    if (row.type === "reasoning-only") {
      // 运行期拆分行：只渲染推理块（正文行被移到工具行之后，时序正确）
      const m = row.data;
      return (
        <div className="message-row assistant">
          <div className="message-avatar agent-avatar thinking">{assistantAvatar}</div>
          <div className="message-col">
            <div className="assistant-body">
              {m.reasoning && m.reasoning.trim() ? (
                <ReasoningBlock text={m.reasoning} streaming={loading} />
              ) : null}
            </div>
          </div>
        </div>
      );
    }

    if (row.type === "tools") {
      // If the only (or last) tool is "clarify", render the interactive
      // question UI instead of the generic tool card.
      const clarifyItem = row.items.find((m) => m.toolName === "clarify");
      if (clarifyItem && row.items.length === 1) {
        return (
          <ClarifyQuestionRow
            toolMsg={clarifyItem}
            assistantAvatar={assistantAvatar}
            onSend={onSend}
          />
        );
      }
      return (
        <ToolsRow
          items={row.items}
          assistantAvatar={assistantAvatar}
          inCurrentTurn={timeline && timeline.length > 0 ? loading : loading && index > lastAssistantRowIdx}
          toolStatus={toolStatus}
          onImageClick={handleImageClick}
          onViewInSidebar={() => onOpenPreviewUrl && onOpenPreviewUrl("tab:artifacts")}
        />
      );
    }

    // Regular message
    const m = row.data;
    // 运行期拆分出来的正文行：不再渲染推理块（推理在 reasoning-only 行）
    const suppressReasoning = !!row._splitText;
    const isUser = m.role === "user";
    const isError = m.isError;
    const isLast = isLastRow;
    const isStreamingText = isLast && !isUser && loading && streamPhase === "text_generating";
    const isThinkingInline = !isUser && row._thinking; // inline thinking inside this bubble
    // 本回合仍在写的 assistant 消息：推理块据此保持展开（回合结束 → loading=false → 自动收纳）。
    const isCurrentAssistantTurn = !isUser && loading && !!currentAssistantMsgId && m.id === currentAssistantMsgId;
    // Live deep-reasoning stream: reasoning.delta 实时 patch 到 m.reasoning
    // （2026-08-26 起），所以优先用 m.reasoning；reasoningText 仅作兜底。
    // 此前 ReasoningBlock 只在完成态分支渲染 —— 思考阶段（_thinking 行）和
    // 文字生成阶段（isStreamingText 行）都看不到推理流，用户反馈"看不到
    // thinking 的过程"。现在三个分支都能流式显示。
    const liveReasoning = !isUser && !suppressReasoning
      ? (m.reasoning && m.reasoning.trim()
          ? m.reasoning
          : (isLast && loading && reasoningText && reasoningText.trim() ? reasoningText : ""))
      : "";
    const isEditing = editingMessageId && m.id === editingMessageId;
    const mctx = mentionCtx[m.id];
    const isMentionUser = isUser && !!mctx?.self;
    const isInner = !isUser && !!mctx?.inner;
    const rawContent = isMentionUser
      ? String(m.content || "").replace(/^\s*@([^\s@]+)/, "").trim()
      : m.content;
    // For user messages, extract data-URL images BEFORE markdown rendering.
    // This avoids passing 2MB+ base64 strings through ReactMarkdown on every
    // stream token, which chokes Virtuoso (re-parse → <img> never decodes).
    const extracted = isUser ? cachedExtract(rawContent) : null;
    const displayContent = extracted ? extracted.text : rawContent;
    const userImages = extracted?.images || null;
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
              // Inline thinking: compact spinner + workflow progress AND the
              // full ThinkingTranscript (previously only the spinner showed,
              // making the thinking text invisible once text_generating kicked
              // in). Both rows stay mounted so the user can watch reasoning
              // accumulate while the typed reply streams in below.
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
                  {backendSilentMs > 45000 && (
                    <div className="btc-health">
                      ⏳ 后端已静默 {formatSilentMs(backendSilentMs)} —— 通常是长命令执行或模型思考中（SSE 连接正常）
                    </div>
                  )}
                  {latestProgress && (
                    <div className="btc-progress">
                      <span className="btc-stage">{latestProgress.stage || latestProgress.step_id}</span>
                      {latestProgress.total > 0 && (
                        <span className="btc-pos">{latestProgress.completed}/{latestProgress.total}</span>
                      )}
                    </div>
                  )}
                </div>
                <ThinkingTranscript text={thinkingText} collapsed={false} />
                {liveReasoning && !isDuplicateReasoning(liveReasoning, "") && (
                  <ReasoningBlock text={liveReasoning} streaming={true} />
                )}
                {/* ArtifactPreview 唯一出口在 ToolsRow —— 这里重复渲染会让
                    运行期产物 chip 出现两次（2026-08-27）。 */}
              </>
            ) : isStreamingText ? (
              <>
                {liveReasoning && !isDuplicateReasoning(liveReasoning, typeof displayContent === "string" ? displayContent : "") && (
                  <ReasoningBlock text={liveReasoning} streaming={true} />
                )}
                <TypewriterText
                  content={sanitizeMessageContent(normalizeContentForTypewriter(displayContent))}
                  isStreaming={true}
                  onImageClick={handleImageClick}
                />
              </>
            ) : (
              <>
                {/* Standalone data-URL images for user messages — rendered as
                    compact chips with hover thumbnails so they don't dominate
                    the bubble. */}
                {userImages && userImages.length > 0 && (
                  <div className="user-attached-images">
                    {userImages.map((img, i) => (
                      <ImageChip
                        key={`uimg-${i}`}
                        src={img.src}
                        fileName={img.alt || `图片${i + 1}`}
                        onClick={() => handleImageClick(img.src, img.alt)}
                      />
                    ))}
                  </div>
                )}
                {/* ReasoningBlock moved ABOVE the answer: per the 2026-08-24 UI
                    pass, the 深度推理 panel now sits above formatContent so
                    the model "thinks out loud" first, then the answer follows.
                    `streaming` tells the block whether to auto-collapse once
                    the answer is done (the block itself implements that). */}
                {!isUser &&
                  liveReasoning &&
                  !isDuplicateReasoning(liveReasoning, displayContent) && (
                    <ReasoningBlock
                      key="reasoning-block"
                      text={liveReasoning}
                      streaming={isCurrentAssistantTurn}
                    />
                  )}
                {formatContent(displayContent, handleImageClick, onOpenPreviewUrl)}
              </>
            )}
          </div>
          {!isEditing && !isThinkingInline && !isStreamingText && (
            <MessageActions
              message={row._timeline && m._realId ? { ...m, id: m._realId } : m}
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
        </div>
      </div>
    );
  };

  return (
    <div className="vs-wrapper">
      <div ref={setScrollParent} className="vs-container">
        <Virtuoso
          key={scrollParent ? "vs-mounted" : "vs-pending"}
          ref={virtuosoRef}
          className="vs-content"
          customScrollParent={scrollParent}
          data={rows}
          initialTopMostItemIndex={rows.length > 0 ? rows.length - 1 : 0}
          followOutput={(isAtBottom) => (isAtBottom ? "smooth" : false)}
          atBottomStateChange={setAtBottom}
          computeItemKey={computeItemKey}
          itemContent={itemContent}
          components={{ List: CenteredList, Footer: MessageFooter }}
          context={{
            uiBlocks,
            subagents,
            moaRefs,
            moaAggregating,
            reviewSummary,
            approval,
            onRespondApproval,
            currentTurnToolMessages,
            onOpenPreviewUrl,
          }}
        />
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

// React.memo prevents re-render cascade from parent (ChatLayout → App) on
// every stream token.  Only when messages / loading / streamPhase actually
// change will the rows be rebuilt and Virtuoso updated.
//
// NOTE: export default is intentionally at the bottom of the file so the
// stable MessageFooter component (defined after the P1 helpers) can be placed
// near the components it assembles.
//
// ─────────────────────────────────────────────────────────────────────────
// P1 增量 UI 组件（深度推理 / 子 agent / MOA / 评审摘要）
// 这些组件只依赖 Footer 传入的瞬时 props，独立渲染，不进入 Virtuoso 行重算。
// ─────────────────────────────────────────────────────────────────────────

/**
 * ReasoningBlock — 模型深度推理 token 的折叠展示（区别于浅层 thinking 指示）。
 * Sits ABOVE the answer (moved in 2026-08-24 UI pass). `streaming` controls
 * the open/collapse cycle:
 *   - while the agent is still streaming this turn -> open
 *   - when streaming ends -> auto-collapse
 *   - if the user manually toggles open -> respect that until the next
 *     streaming cycle starts (a `userToggledRef` records the override so
 *     the auto-collapse won't fight the user)
 * Renders inside a scrollable translucent bubble, auto-scrolls to the
 * bottom as new reasoning tokens stream in, and pauses auto-scroll when
 * the user scrolls up.
 */
function ReasoningBlock({ text, streaming = false }) {
  // Default folded so the thinking card never dominates the chat surface.
  // Users can click the toggle to inspect it; their choice is preserved.
  const [open, setOpen] = useState(false);
  const bubbleRef = useRef(null);
  const userScrolledRef = useRef(false);
  const userToggledRef = useRef(false);
  const display = text || "";

  // 2026-08-27 交互约定：流式进行中自动展开（用户实时看推理内容），
  // 回合结束自动收纳。用户手动切换过则尊重其选择，直到下一个流式周期
  // （streaming 再次变为 true 时重置手动覆盖）。
  useEffect(() => {
    if (streaming) {
      userToggledRef.current = false;
      setOpen(true);
    } else if (!userToggledRef.current) {
      setOpen(false);
    }
  }, [streaming]);

  useEffect(() => {
    const el = bubbleRef.current;
    if (!el || !open) return;
    if (!userScrolledRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [display, open]);

  const handleScroll = useCallback(() => {
    const el = bubbleRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;
    userScrolledRef.current = !nearBottom;
  }, []);

  const toggle = useCallback(() => {
    userToggledRef.current = true;
    setOpen((o) => !o);
  }, []);

  return (
    <div className={`reasoning-block ${streaming ? "is-streaming" : "is-done"}`}>
      <button className="reasoning-toggle" onClick={toggle}>
        <span className="reasoning-icon"><Icon name="lightbulb" size={14} /></span>
        <span className="reasoning-text">深度推理</span>
        <span className="reasoning-detail">{display.length} 字符</span>
        <span
          className={`reasoning-status ${streaming ? "running" : "complete"}`}
          style={streaming ? {
            color: "#d29922",
            animation: "status-running-pulse 1.5s ease-in-out infinite",
          } : undefined}
        >
          {streaming ? "思考中…" : "已完成"}
        </span>
        <span className={`reasoning-chevron ${open ? "expanded" : ""}`}>
          <Icon name="chevron" size={12} style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }} />
        </span>
      </button>
      <div
        ref={bubbleRef}
        className={`reasoning-bubble ${open ? "" : "collapsed"}`}
        onScroll={handleScroll}
      >
        {display}
      </div>
    </div>
  );
}

/**
 * MoaBlock — MOA 多模型聚合参考（moa.reference / moa.aggregating）。
 */
function MoaBlock({ refs, aggregating }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="moa-block">
      <button className="moa-toggle" onClick={() => setOpen((o) => !o)}>
        <Icon name="shuffle" size={13} />
        <span>多模型参考 (MOA)</span>
        {aggregating && <span className="moa-aggregating">聚合中…</span>}
        <span className="moa-count">{refs.length}</span>
        <Icon name="chevron" size={12} style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }} />
      </button>
      {open && (
        <div className="moa-list">
          {refs.map((r, i) => (
            <div className="moa-ref" key={i}>
              <span className="moa-ref-label">{r.label || `参考 ${i + 1}`}</span>
              <span className="moa-ref-text">{r.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * ReviewSummaryBlock — 评审 / 自检摘要（review.summary）。
 */
function ReviewSummaryBlock({ text }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="review-summary-block">
      <button className="review-toggle" onClick={() => setOpen((o) => !o)}>
        <Icon name="clipboard" size={13} />
        <span>评审摘要</span>
        <Icon name="chevron" size={12} style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }} />
      </button>
      {open && <pre className="review-text">{text}</pre>}
    </div>
  );
}


/**
 * MessageFooter — Virtuoso Footer 组件。
 *
 * 之前 Virtuoso 的 `components.Footer` 用内联箭头函数定义，导致每次
 * MessageThread 重渲染时 Footer 组件类型都变，React 会把整个 Footer 子树
 * 卸载并重新挂载。结果 ReviewSummaryBlock 等带内部 state 的 P1 组件在每次
 * 流式 token 到来时都丢失状态，表现为「评审摘要无法展开」。
 *
 * 把这个 Footer 提取为模块级稳定组件，动态数据通过 Virtuoso 的 `context`
 * 传入；context 变化只会触发重渲染，不会导致子树 remount，因此
 * ReviewSummaryBlock 的展开状态可以保留。
 */
function MessageFooter({ context }) {
  const {
    uiBlocks,
    subagents,
    moaRefs,
    moaAggregating,
    reviewSummary,
    approval,
    onRespondApproval,
    currentTurnToolMessages,
  } = context || {};
  return (
    <>
      {uiBlocks?.length > 0 && (
        <div className="ui-blocks-region" key="ui-blocks-region">
          {uiBlocks.map((b) => (
            <GeneratedComponent key={b.blockId} block={b} />
          ))}
        </div>
      )}
      {subagents?.length > 0 && null /* SubagentTerminal 移除：subagent 状态由 AgentRunMonitor（Composer 上方）承载，inner message 文本表达完成 */}
      {moaRefs?.length > 0 && (
        <MoaBlock key="moa-block" refs={moaRefs} aggregating={moaAggregating} />
      )}
      {reviewSummary && (
        <ReviewSummaryBlock key="review-summary" text={reviewSummary} />
      )}
      {/* 审批气泡已迁移至 Composer（小 Bach 头顶气泡，2026-08-27），此处不再渲染 */}
    </>
  );
}

export default React.memo(MessageThread);
