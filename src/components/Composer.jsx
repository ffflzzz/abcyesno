import React, { useState, useRef, useEffect } from "react";
import Icon from "./Icon.jsx";
import bachPeek from "../assets/bach-peek.png";
import ApprovalBubble from "./ApprovalBubble.jsx";

const AGNES_MODELS = [
  { id: "agnes-2.5-flash", name: "agnes-2.5-flash", tag: "快" },
  { id: "__custom__", name: "自定义模型...", tag: "" },
];

// Only two modes are real in Hermes: default (backend "ask") and yolo
// (session approval bypass). "strict" (ask on every call) has no native
// backend support, so it is intentionally omitted — no fake switch.
const PERMISSION_MODES = [
  { id: "default", name: "默认权限", desc: "工具按后端默认策略自动批准" },
  { id: "yolo", name: "完全自动", desc: "本会话不再询问，直接执行" },
];

// ── Workspace (per-session working folder, docs/SESSION_WORKSPACE_SPEC.md) ──
// Recent folders live in localStorage — pure UI convenience; the binding
// itself is per-session state persisted by the caller via updateSession.
const WS_RECENT_KEY = "composer-recent-workspaces";
const WS_RECENT_MAX = 8;

function wsBasename(dir) {
  if (!dir) return "";
  const norm = String(dir).replace(/[\\/]+$/, "");
  const idx = Math.max(norm.lastIndexOf("\\"), norm.lastIndexOf("/"));
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}

function loadRecentWorkspaces() {
  try {
    const raw = JSON.parse(localStorage.getItem(WS_RECENT_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((d) => typeof d === "string" && d).slice(0, WS_RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function rememberWorkspace(dir) {
  if (!dir) return;
  const next = [dir, ...loadRecentWorkspaces().filter((d) => d !== dir)].slice(0, WS_RECENT_MAX);
  try {
    localStorage.setItem(WS_RECENT_KEY, JSON.stringify(next));
  } catch {
    /* quota/private mode — recents are best-effort */
  }
}

// Remove one entry from the recents list. Pure UI hygiene: recents are a
// localStorage convenience cache, so forgetting a path never touches the
// folder on disk nor any session's existing workspaceDir binding.
export function forgetWorkspace(dir) {
  if (!dir) return;
  try {
    const next = loadRecentWorkspaces().filter((d) => d !== dir);
    localStorage.setItem(WS_RECENT_KEY, JSON.stringify(next));
  } catch {
    /* best-effort */
  }
}

// Slash commands surfaced by the `/` command palette. This list mirrors what
// the 9120 gateway's `command.dispatch` actually implements (see
// tui_gateway/server.py). Commands that are TUI-only on the backend
// (/model, /status, /fast, /clear, /new, …) are intentionally omitted — typing
// them still sends the text to the LLM, but they are not advertised here.
const SLASH_COMMANDS = [
  { cmd: "/goal", desc: "设一个长期目标，Agent 自动多轮循环直到完成（后接目标描述）" },
  { cmd: "/goal status", desc: "查看当前目标状态" },
  { cmd: "/goal pause", desc: "暂停当前目标" },
  { cmd: "/goal resume", desc: "恢复暂停的目标" },
  { cmd: "/goal clear", desc: "清除当前目标" },
  { cmd: "/undo", desc: "回退最近 N 轮对话（可加数字，如 /undo 3）" },
  { cmd: "/retry", desc: "重试上一轮" },
  { cmd: "/steer", desc: "运行中转向（后接新指令）" },
  { cmd: "/queue", desc: "排队一条消息（后接内容）" },
  { cmd: "/learn", desc: "学习任务并写成技能（后接主题）" },
  { cmd: "/moa", desc: "单次多模型聚合（后接提示）" },
  { cmd: "/snapshot", desc: "快照（TUI 受限，桌面端仅提示）" },
];

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ── Rich composer (contenteditable) helpers ────────────────────────────
// Images are inserted as contentEditable=false chips (filename + hover
// thumbnail) so they don't dominate the composer. On send we serialize each
// chip → a positional placeholder [[IMG:i]]; the i-th image's dataUrl is
// stashed in imagesRef. ChatLayout restores [[IMG:i]] to a markdown image so
// the model sees the picture exactly where the user placed it.

function makeInlineImageChip(fileName, dataUrl, idx) {
  // 2026-08-28：chip 内嵌真实缩略图（此前只有文件名文本，悬停预览用的
  // CSS attr(data-img-src) 在 Chromium 不生效——用户根本看不到图片预览）。
  const chip = document.createElement("span");
  chip.className = "composer-image-chip";
  chip.setAttribute("data-inline-img", String(idx));
  chip.setAttribute("data-img-src", dataUrl);
  chip.setAttribute("contentEditable", "false");
  chip.setAttribute("title", fileName || "图片");
  const img = document.createElement("img");
  img.className = "composer-image-chip-thumb";
  img.src = dataUrl;
  img.alt = fileName || "图片";
  img.draggable = false;
  chip.appendChild(img);
  const label = document.createElement("span");
  label.className = "composer-image-chip-name";
  label.textContent = fileName || "图片";
  chip.appendChild(label);
  return chip;
}

// Insert a node at the current caret inside `container`; caret then moves
// just after it so the user keeps typing. Falls back to appending at the end.
function insertNodeAtCaret(container, node) {
  node.setAttribute("contentEditable", "false");
  const sel = window.getSelection();
  let range = null;
  if (sel && sel.rangeCount > 0) {
    const r = sel.getRangeAt(0);
    if (container.contains(r.commonAncestorContainer)) range = r;
  }
  if (!range) {
    range = document.createRange();
    range.selectNodeContents(container);
    range.collapse(false);
  }
  range.deleteContents();
  range.insertNode(node);
  // zero-width space after the image gives the caret a text node to land in.
  const zwsp = document.createTextNode(" ");
  node.parentNode && node.parentNode.insertBefore(zwsp, node.nextSibling);
  const after = document.createRange();
  after.setStartAfter(zwsp);
  after.collapse(true);
  if (sel) {
    sel.removeAllRanges();
    sel.addRange(after);
  }
}

// Serialize the editable surface to plain text + [[IMG:i]] placeholders.
function serializeEditable(el) {
  let out = "";
  el.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.nodeValue.replace(/ /g, "");
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = node.tagName;
      const inlineIdx = node.getAttribute && node.getAttribute("data-inline-img");
      if (inlineIdx != null) {
        out += `[[IMG:${inlineIdx}]]`;
      } else if (tag === "BR") {
        out += "\n";
      } else if (tag === "DIV" || tag === "P") {
        if (out && !out.endsWith("\n")) out += "\n";
        out += serializeEditable(node);
        if (!out.endsWith("\n")) out += "\n";
      } else {
        out += serializeEditable(node);
      }
    }
  });
  return out;
}

// Strip the dataURL prefix so we ship raw base64 to the backend.
function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export default function Composer({
  model,
  onModelChange,
  onSend,
  onStop,
  onNewSession,
  onUpload,
  onShowSkills,
  onOpenBrowser,
  disabled,
  placeholder,
  attachment,
  onClearAttachment,
  permission,
  onPermissionChange,
  workspace = null,
  onWorkspaceChange,
  onPickWorkspace,
  busy = false,
  queuedMessages = [],
  onRemoveQueued,
  mentionables = [],
  onOpenPreviewUrl,
  runError = null,
  onClearRunError,
  approval = null,
  onRespondApproval,
}) {
  const [empty, setEmpty] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const [sending, setSending] = useState(false);
  const [customModel, setCustomModel] = useState("");
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [showCustomModel, setShowCustomModel] = useState(false);
  const [showPermissionMenu, setShowPermissionMenu] = useState(false);
  const [showWorkspaceMenu, setShowWorkspaceMenu] = useState(false);
  const [recentWorkspaces, setRecentWorkspaces] = useState(loadRecentWorkspaces);
  const [showPlusMenu, setShowPlusMenu] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recSeconds, setRecSeconds] = useState(0);
  const [voiceError, setVoiceError] = useState("");
  const editableRef = useRef(null);
  const imagesRef = useRef([]); // dataUrl per [[IMG:i]] placeholder
  const rootRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const mediaChunksRef = useRef([]);
  const mediaStreamRef = useRef(null);
  const recTimerRef = useRef(null);
  // @ mention protocol (spec §2): typing `@` opens an entity picker; the
  // selected entity is inserted as a `@name` token. On send we derive the
  // mention list by scanning the text, so stale refs never misroute.
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const mentionListRef = useRef(null);
  // @ mention protocol (spec §2): typing `@` opens an entity picker; the
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const slashListRef = useRef(null);

  // Recompute emptiness (drives placeholder) from the live DOM.
  const recomputeEmpty = () => {
    const el = editableRef.current;
    if (!el) return;
    const hasImg = !!el.querySelector("img[data-inline-img]");
    const txt = (el.innerText || "").replace(/ /g, "").trim();
    setEmpty(!hasImg && txt.length === 0);
  };

  useEffect(() => {
    if (!disabled) setSending(false);
  }, [disabled]);

  // Auto-focus the contentEditable div on mount (e.g. after session switch remount)
  useEffect(() => {
    const el = editableRef.current;
    if (el && !disabled) {
      // Small delay to ensure DOM is fully painted after React commit
      const timer = setTimeout(() => el.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Close all popovers on outside click
  useEffect(() => {
    if (!showModelMenu && !showPermissionMenu && !showPlusMenu && !showWorkspaceMenu && !showCustomModel && !mentionOpen && !slashOpen) return;
    const handleClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setShowModelMenu(false);
        setShowPermissionMenu(false);
        setShowPlusMenu(false);
        setShowWorkspaceMenu(false);
        setShowCustomModel(false);
        setMentionOpen(false);
        setSlashOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showModelMenu, showPermissionMenu, showPlusMenu, showWorkspaceMenu, showCustomModel, mentionOpen, slashOpen]);

  // ── @ mention protocol (spec §2) ──────────────────────────────────────
  // Inspect the text immediately before the caret; if it ends with `@word`
  // (no spaces), open the entity picker filtered by `word`.
  function detectMentionTrigger() {
    const el = editableRef.current;
    if (!el) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!el.contains(range.commonAncestorContainer)) return;
    let textBefore = "";
    const node = range.startContainer;
    const offset = range.startOffset;
    if (node.nodeType === Node.TEXT_NODE) {
      textBefore = node.nodeValue.slice(0, offset);
    } else {
      const r = document.createRange();
      r.selectNodeContents(el);
      r.setEnd(range.startContainer, range.startOffset);
      textBefore = r.toString();
    }
    const m = textBefore.match(/@([^\s@]*)$/);
    if (m) {
      setMentionQuery(m[1]);
      setMentionOpen(true);
    } else {
      setMentionOpen(false);
    }
  }

  // ── / command palette ───────────────────────────────────────────────
  // Inspect the text immediately before the caret; if it ends with `/word`
  // (no spaces yet → still typing the command name, and word has no slash so
  // it isn't a path like /Users/foo), open the command palette filtered by
  // `word`. Once a space is typed (entering args) the palette closes.
  function detectSlashTrigger() {
    const el = editableRef.current;
    if (!el) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!el.contains(range.commonAncestorContainer)) return;
    let textBefore = "";
    const node = range.startContainer;
    const offset = range.startOffset;
    if (node.nodeType === Node.TEXT_NODE) {
      textBefore = node.nodeValue.slice(0, offset);
    } else {
      const r = document.createRange();
      r.selectNodeContents(el);
      r.setEnd(range.startContainer, range.startOffset);
      textBefore = r.toString();
    }
    const m = textBefore.match(/(?:^|\s)\/([^\s/]*)$/);
    if (m) {
      setSlashQuery(m[1]);
      setSlashOpen(true);
    } else {
      setSlashOpen(false);
    }
  }

  // Replace the trailing `/query` at the caret with the chosen `/command `.
  function applySlash(item) {
    const el = editableRef.current;
    const sel = window.getSelection();
    if (!el || !sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    const node = range.startContainer;
    const offset = range.startOffset;
    if (node.nodeType === Node.TEXT_NODE) {
      const before = node.nodeValue.slice(0, offset);
      const after = node.nodeValue.slice(offset);
      const mm = before.match(/(?:^|\s)\/([^\s/]*)$/);
      if (mm) {
        const insertText = item.cmd + " ";
        const newBefore = before.slice(0, mm.index) + insertText;
        node.nodeValue = newBefore + after;
        const newRange = document.createRange();
        newRange.setStart(node, newBefore.length);
        newRange.collapse(true);
        sel.removeAllRanges();
        sel.addRange(newRange);
      }
    }
    setSlashOpen(false);
    recomputeEmpty();
    el.focus();
  }

  // Replace the trailing `@query` at the caret with `@name ` and record it.
  function applyMention(item) {
    const el = editableRef.current;
    const sel = window.getSelection();
    if (!el || !sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    const node = range.startContainer;
    const offset = range.startOffset;
    if (node.nodeType === Node.TEXT_NODE) {
      const before = node.nodeValue.slice(0, offset);
      const after = node.nodeValue.slice(offset);
      const mm = before.match(/@([^\s@]*)$/);
      if (mm) {
        const insertText = `@${item.name} `;
        const newBefore = before.slice(0, mm.index) + insertText;
        node.nodeValue = newBefore + after;
        const newRange = document.createRange();
        newRange.setStart(node, newBefore.length);
        newRange.collapse(true);
        sel.removeAllRanges();
        sel.addRange(newRange);
      }
    }
    setMentionOpen(false);
    recomputeEmpty();
    el.focus();
  }

  const mentionMatches = mentionables.filter((m) => {
    const q = mentionQuery.trim().toLowerCase();
    if (!q) return true;
    return (
      (m.name || "").toLowerCase().includes(q) ||
      (m.id || "").toLowerCase().includes(q)
    );
  });

  const slashMatches = SLASH_COMMANDS.filter((c) => {
    const q = slashQuery.trim().toLowerCase();
    if (!q) return true;
    return c.cmd.toLowerCase().includes(q);
  });

  // Derive the mention id list from the final text by matching `@name`/`@id`
  // tokens against the known entity directory.
  function deriveMentions(text) {
    const ids = [];
    const re = /@([^\s@]+)/g;
    let mm;
    while ((mm = re.exec(text)) !== null) {
      const token = mm[1];
      const hit = mentionables.find(
        (m) => m.name === token || m.id === token ||
          m.name === token.replace(/_/g, " ") || m.id === token.replace(/\s+/g, "_")
      );
      if (hit && !ids.includes(hit.id)) ids.push(hit.id);
    }
    return ids;
  }

  const handleInput = () => {
    recomputeEmpty();
    detectMentionTrigger();
    detectSlashTrigger();
  };

  const submitCore = (steer) => {
    const el = editableRef.current;
    const raw = el ? serializeEditable(el).trim() : "";
    const hasImage = !!(el && el.querySelector("img[data-inline-img]"));
    const hasContent = raw.length > 0 || hasImage;

    // Hard disabled (approval gate / backend down): the button acts as Stop.
    if (disabled) {
      if (onStop) onStop();
      return;
    }
    // Busy with an empty box: button acts as Stop.
    if (busy && !hasContent) {
      if (onStop) onStop();
      return;
    }
    if (sending) return;
    if (!hasContent) return;
    setSending(true);
    const content = raw;
    const images = imagesRef.current.slice();
    const mentions = deriveMentions(content);
    // Clear the editable surface + image stash.
    if (el) el.innerHTML = "";
    imagesRef.current = [];
    setEmpty(true);
    setMentionOpen(false);
    // When busy, onSend queues this behind the current run; when idle it
    // sends immediately. Either way the composer clears and stays editable.
    // 2026-08-31 steer：busy 时第 4 参 {steer:true} 走插队注入（不打断），
    // 未生效时 App 层退回排队；空闲时 steer 等价普通发送。
    Promise.resolve(onSend(content, images, mentions, steer ? { steer: true } : {})).catch((err) => {
      console.error("send failed", err);
    }).finally(() => {
      setSending(false);
    });
  };

  const submit = () => submitCore(false);
  // 插队发送：busy 时把内容注入当前运行中的任务（不打断、排队语义不变）。
  const submitSteer = () => submitCore(true);

  // Insert an image inline at the caret; stash its dataUrl for send.
  const insertImageInline = (fileName, dataUrl) => {
    const el = editableRef.current;
    if (!el) return;
    el.focus();
    const idx = imagesRef.current.length;
    imagesRef.current.push(dataUrl);
    insertNodeAtCaret(el, makeInlineImageChip(fileName, dataUrl, idx));
    recomputeEmpty();
  };

  const handlePaste = async (e) => {
    const cd = e.clipboardData;
    const files = cd && cd.files;
    if (files && files.length > 0) {
      e.preventDefault();
      for (const file of files) {
        if (file.type && file.type.startsWith("image/")) {
          try {
            const dataUrl = await readFileAsDataURL(file);
            insertImageInline(file.name || "", dataUrl);
          } catch (err) {
            console.error("paste image failed", err);
          }
        }
      }
      return;
    }
    // Plain-text paste: strip formatting, keep it inline at the caret.
    e.preventDefault();
    const txt = (cd && cd.getData("text/plain")) || "";
    if (txt) document.execCommand("insertText", false, txt);
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    setDragOver(false);
    const files = e.dataTransfer && e.dataTransfer.files;
    if (!files || files.length === 0) return;
    const file = files[0];
    const filePath = file.path || file.name;
    if (file.type && file.type.startsWith("image/")) {
      try {
        const dataUrl = await readFileAsDataURL(file);
        insertImageInline(file.name || "", dataUrl);
      } catch (err) {
        console.error("drop image failed", err);
      }
    } else {
      if (onUpload) onUpload({ type: "file", fileName: file.name, filePath });
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    // 2026-08-31 插队：Ctrl/Cmd+Enter 忙时立即注入当前任务（不打断）。
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.shiftKey) {
      e.preventDefault();
      if (busy) submitSteer();
      else submit();
    }
  };

  // ── Voice input (STT) ──────────────────────────────────────────────
  const startRecording = async () => {
    setVoiceError("");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof window.MediaRecorder === "undefined") {
      setVoiceError("当前环境不支持语音录制");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const mr = new MediaRecorder(stream);
      mediaChunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) mediaChunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        const blob = new Blob(mediaChunksRef.current, { type: mr.mimeType || "audio/webm" });
        if (mediaStreamRef.current) {
          mediaStreamRef.current.getTracks().forEach((t) => t.stop());
          mediaStreamRef.current = null;
        }
        await handleTranscribe(blob, mr.mimeType || "audio/webm");
      };
      mr.start();
      mediaRecorderRef.current = mr;
      setRecording(true);
      setRecSeconds(0);
      recTimerRef.current = setInterval(() => setRecSeconds((s) => s + 1), 1000);
    } catch (err) {
      setVoiceError("无法访问麦克风：" + (err && err.message ? err.message : String(err)));
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    if (recTimerRef.current) {
      clearInterval(recTimerRef.current);
      recTimerRef.current = null;
    }
    setRecording(false);
  };

  const toggleRecording = () => {
    if (recording) stopRecording();
    else startRecording();
  };

  const handleTranscribe = async (blob, mime) => {
    try {
      const b64 = await blobToBase64(blob);
      if (!window.hermes || !window.hermes.transcribeAudio) {
        setVoiceError("语音转写未启用（后端不可用）");
        return;
      }
      const res = await window.hermes.transcribeAudio(b64, mime);
      if (res && res.error) {
        setVoiceError("转写失败：" + res.error);
        return;
      }
      const text = res && res.text ? String(res.text).trim() : "";
      if (text) {
        const el = editableRef.current;
        if (el) {
          el.focus();
          document.execCommand("insertText", false, text);
          recomputeEmpty();
        }
      } else {
        setVoiceError("未识别到文字");
      }
    } catch (err) {
      setVoiceError("转写异常：" + (err && err.message ? err.message : String(err)));
    }
  };

  // Clean up mic/recorder on unmount.
  useEffect(() => () => {
    if (recTimerRef.current) clearInterval(recTimerRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try { mediaRecorderRef.current.stop(); } catch (_) {}
    }
    if (mediaStreamRef.current) mediaStreamRef.current.getTracks().forEach((t) => t.stop());
  }, []);

  const currentModelLabel = () => {
    if (AGNES_MODELS.some((m) => m.id === model)) {
      return model || "agnes-2.5-flash";
    }
    return model || "agnes-2.5-flash";
  };

  const currentPermission = PERMISSION_MODES.find((p) => p.id === permission) || PERMISSION_MODES[0];

  return (
    <div
      ref={rootRef}
      className={`composer-shell ${dragOver ? "drag-over" : ""} ${busy ? "bach-busy" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {/* ── Bach peeking over the composer ──
         Error state: red dot on the avatar + a small bubble above showing a
         one-line summary. Click the bubble (or the avatar) to expand the full
         error text; close button clears it. Replaces the old chat-wide
         error-banner that spanned the whole top of the chat (too alarming). */}
      {runError && (
        <button
          className="composer-error-close"
          onClick={() => onClearRunError && onClearRunError()}
          title="关闭错误"
          aria-label="关闭错误"
        >
          <Icon name="close" size={11} />
        </button>
      )}
      <div
        className={`composer-bach-peek ${runError ? "has-error" : ""}`}
        onClick={runError ? () => onClearRunError && onClearRunError() : () => onOpenPreviewUrl?.('https://abcyesno.cn')}
        title={runError ? `有错误：${runError.slice(0, 60)}…` : (busy ? "巴赫正在工作中… 点击访问官网" : "巴赫在等你~ 点击访问官网")}
      >
        <img src={bachPeek} alt="Bach" draggable={false} />
        {runError && <span className="composer-error-dot" aria-hidden="true" />}
      </div>
      {runError && (
        <div className="composer-error-bubble" role="status">
          <div className="composer-error-bubble-title">⚠ 出错了</div>
          <div className="composer-error-bubble-text">{runError}</div>
          <button
            className="composer-error-bubble-dismiss"
            onClick={() => onClearRunError && onClearRunError()}
          >
            知道了
          </button>
        </div>
      )}
      {/* ── 审批气泡（2026-08-27 重设计）──
         从小 Bach 头顶冒出的紧凑气泡：右下锚定、尾巴指向 Bach、轻微上浮入场。
         替代旧的 chat 流末尾渲染（位置偏移、动静大）。空内容时窄条收起。 */}
      {approval && (
        <div className="composer-approval-pop" role="dialog" aria-label="操作确认">
          <ApprovalBubble approval={approval} onRespond={onRespondApproval} />
        </div>
      )}
      {/* ── File attachment chip (non-image files still go through upload) ── */}
      {attachment && attachment.type !== "image" && (
        <div className="composer-attachment-row">
          <div className="composer-attachment">
            <span className="attachment-file-icon"><Icon name="note" size={14} /></span>
            <span className="attachment-name" title={attachment.fileName}>{attachment.fileName}</span>
            <button className="attachment-clear" onClick={onClearAttachment} title="移除附件">
              <Icon name="close" size={14} />
            </button>
          </div>
        </div>
      )}

      {/* ── Queued messages (typed while agent is busy) ── */}
      {queuedMessages.length > 0 && (
        <div className="composer-queue">
          {queuedMessages.map((m) => (
            <div key={m.id} className="composer-queue-item">
              <span className="composer-queue-icon" title="排队中"><Icon name="loader" size={14} /></span>
              <span className="composer-queue-text">{m.text}</span>
              <button
                className="composer-queue-remove"
                onClick={() => onRemoveQueued && onRemoveQueued(m.id)}
                title="移除"
              >
                <Icon name="close" size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ── Top: rich input area (text + inline images) ── */}
      <div className="composer-editable-wrap">
        {empty && (
          <div className="composer-placeholder">
            {placeholder || "今天帮你做些什么？ 输入 / 调用命令（/goal、/undo…），@ 引用对话/工作流"}
          </div>
        )}
        <div
          ref={editableRef}
          className="composer-input composer-editable"
          contentEditable={!disabled}
          suppressContentEditableWarning
          onInput={handleInput}
          onKeyDown={onKeyDown}
          onPaste={handlePaste}
          data-disabled={disabled ? "true" : undefined}
        />
        {mentionOpen && mentionMatches.length > 0 && (
          <div className="mention-popover" ref={mentionListRef}>
            <div className="mention-popover-head">提及调用</div>
            {mentionMatches.map((m) => (
              <button
                key={m.id}
                className="mention-item"
                onClick={() => applyMention(m)}
                title={`调用 ${m.name}`}
              >
                <span className={`mention-kind ${m.kind}`}>{m.kind === "workflow" ? <Icon name="settings" size={14} /> : <Icon name="chat" size={14} />}</span>
                <span className="mention-name">{m.name}</span>
                <span className="mention-id">{m.id}</span>
              </button>
            ))}
          </div>
        )}
        {slashOpen && slashMatches.length > 0 && (
          <div className="mention-popover" ref={slashListRef}>
            <div className="mention-popover-head">命令</div>
            {slashMatches.map((c) => (
              <button
                key={c.cmd}
                className="mention-item"
                onClick={() => applySlash(c)}
                title={c.desc}
              >
                <span className="mention-kind"><Icon name="zap" size={14} /></span>
                <span className="mention-name">{c.cmd}</span>
                <span className="mention-id">{c.desc}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Voice error / status line ── */}
      {voiceError && (
        <div className="composer-voice-error" onClick={() => setVoiceError("")} title="点击关闭">
          <Icon name="warning" size={14} /> {voiceError}
        </div>
      )}

      {/* ── Bottom: action bar ── */}
      <div className="composer-actionbar">
        {/* Left group */}
        <div className="composer-left">
          {/* + menu: new session / upload / skills */}
          <div className="composer-menu-wrap">
            <button
              className="composer-icon-btn"
              title="更多操作"
              onClick={() => { setShowPlusMenu(!showPlusMenu); setShowPermissionMenu(false); setShowModelMenu(false); }}
            >
              +
            </button>
            {showPlusMenu && (
              <div className="composer-popover composer-popover-up">
                <button className="composer-menu-item" onClick={() => { setShowPlusMenu(false); onNewSession && onNewSession(); }}>
                  <span className="menu-icon"><Icon name="plus" size={14} /></span> 新会话
                </button>
                <button className="composer-menu-item" onClick={() => { setShowPlusMenu(false); onUpload && onUpload(); }} disabled={disabled}>
                  <span className="menu-icon"><Icon name="note" size={14} /></span> 上传文件
                </button>
                <button className="composer-menu-item" onClick={() => { setShowPlusMenu(false); onShowSkills && onShowSkills(); }} disabled={disabled}>
                  <span className="menu-icon"><Icon name="zap" size={14} /></span> 技能与工作流
                </button>
                <button className="composer-menu-item" onClick={() => { setShowPlusMenu(false); onOpenBrowser && onOpenBrowser(); }} disabled={disabled}>
                  <span className="menu-icon"><Icon name="globe" size={14} /></span> 打开浏览器
                </button>
              </div>
            )}
          </div>

          {/* Permission selector */}
          <div className="composer-menu-wrap">
            <button
              className="composer-pill-btn"
              title="权限模式"
              onClick={() => { setShowPermissionMenu(!showPermissionMenu); setShowPlusMenu(false); setShowModelMenu(false); setShowWorkspaceMenu(false); }}
            >
              <span className="pill-icon"><Icon name="shield" size={14} /></span>
              <span className="pill-label">{currentPermission.name}</span>
              <span className="pill-caret"><Icon name="chevron" size={12} /></span>
            </button>
            {showPermissionMenu && (
              <div className="composer-popover composer-popover-up">
                {PERMISSION_MODES.map((p) => (
                  <button
                    key={p.id}
                    className={`composer-menu-item ${p.id === permission ? "active" : ""}`}
                    onClick={() => { if (onPermissionChange) onPermissionChange(p.id); setShowPermissionMenu(false); }}
                    title={p.desc}
                  >
                    <span className="menu-icon"><Icon name={p.id === permission ? "dot" : "circle"} size={12} /></span>
                    <span>
                      <div className="menu-item-title">{p.name}</div>
                      <div className="menu-item-desc">{p.desc}</div>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Workspace selector: bind this session to a local folder so file
              tools / terminal resolve relative paths inside it. */}
          <div className="composer-menu-wrap">
            <button
              className={`composer-pill-btn ${workspace ? "ws-active" : ""}`}
              title={workspace || "工作空间：绑定本会话的工作文件夹"}
              onClick={() => { setShowWorkspaceMenu(!showWorkspaceMenu); setShowPlusMenu(false); setShowPermissionMenu(false); setShowModelMenu(false); setRecentWorkspaces(loadRecentWorkspaces()); }}
            >
              <span className="pill-icon"><Icon name="folder" size={14} /></span>
              <span className="pill-label">{workspace ? wsBasename(workspace) : "工作空间"}</span>
              <span className="pill-caret"><Icon name="chevron" size={12} /></span>
            </button>
            {showWorkspaceMenu && (
              <div className="composer-popover composer-popover-up">
                {recentWorkspaces.filter((d) => d !== workspace).map((dir) => (
                  <div key={dir} className="composer-ws-row">
                    <button
                      className="composer-menu-item"
                      title={dir}
                      onClick={() => { setShowWorkspaceMenu(false); if (onWorkspaceChange) onWorkspaceChange(dir); }}
                    >
                      <span className="menu-icon"><Icon name="circle" size={12} /></span>
                      <span>
                        <div className="menu-item-title">{wsBasename(dir)}</div>
                        <div className="menu-item-desc">{dir}</div>
                      </span>
                    </button>
                    <button
                      className="composer-ws-forget"
                      title="从历史列表移除（不影响磁盘上的文件夹）"
                      onClick={(e) => { e.stopPropagation(); forgetWorkspace(dir); setRecentWorkspaces(loadRecentWorkspaces()); }}
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </div>
                ))}
                <button
                  className="composer-menu-item"
                  onClick={() => { setShowWorkspaceMenu(false); onPickWorkspace && onPickWorkspace(); }}
                >
                  <span className="menu-icon"><Icon name="plus" size={14} /></span>
                  <span className="menu-item-title">打开本地文件夹…</span>
                </button>
                {workspace && (
                  <button
                    className="composer-menu-item"
                    onClick={() => { setShowWorkspaceMenu(false); if (onWorkspaceChange) onWorkspaceChange(null); }}
                  >
                    <span className="menu-icon"><Icon name="x" size={12} /></span>
                    <span className="menu-item-title">不使用工作空间</span>
                  </button>
                )}
                {workspace && (
                  <div className="composer-ws-path" title={workspace}>{workspace}</div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right group */}
        <div className="composer-right">
          {/* Model selector */}
          <div className="composer-menu-wrap">
            <button
              className="composer-pill-btn model"
              title="选择模型"
              onClick={() => { setShowModelMenu(!showModelMenu); setShowPlusMenu(false); setShowPermissionMenu(false); setShowWorkspaceMenu(false); }}
              disabled={disabled}
            >
              <span className="pill-icon"><Icon name="plus" size={14} /></span>
              <span className="pill-label">{currentModelLabel()}</span>
              <span className="pill-caret"><Icon name="chevron" size={12} /></span>
            </button>
            {showModelMenu && (
              <div className="composer-popover composer-popover-up composer-popover-right">
                {AGNES_MODELS.map((m) => (
                  <button
                    key={m.id}
                    className={`composer-menu-item ${m.id === model ? "active" : ""}`}
                    onClick={() => {
                      if (m.id === "__custom__") {
                        setShowCustomModel(true);
                        setCustomModel(AGNES_MODELS.some((x) => x.id === model) ? "" : model || "");
                      } else {
                        if (onModelChange) onModelChange(m.id);
                        setShowModelMenu(false);
                      }
                    }}
                  >
                    <span className="menu-icon"><Icon name={m.id === model ? "dot" : "circle"} size={12} /></span>
                    <span className="menu-item-title">{m.name}{m.tag ? ` · ${m.tag}` : ""}</span>
                  </button>
                ))}
                {showCustomModel && (
                  <div className="composer-custom-model">
                    <input
                      type="text"
                      value={customModel}
                      onChange={(e) => setCustomModel(e.target.value)}
                      placeholder="模型 ID"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && customModel.trim()) {
                          if (onModelChange) onModelChange(customModel.trim());
                          setShowCustomModel(false);
                          setShowModelMenu(false);
                        }
                      }}
                      autoFocus
                    />
                    <button onClick={() => {
                      if (customModel.trim() && onModelChange) onModelChange(customModel.trim());
                      setShowCustomModel(false);
                      setShowModelMenu(false);
                    }}>确定</button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Voice (STT) */}
          <button
            className={`composer-icon-btn ${recording ? "recording" : ""}`}
            title={recording ? `停止录音（${recSeconds}s）` : "语音输入"}
            onClick={toggleRecording}
          >
            {recording ? <Icon name="stop" size={16} /> : <Icon name="mic" size={16} />}
          </button>

          {/* Steer (busy-only): inject into the running turn without interrupting */}
          {busy && !empty && !disabled && (
            <button
              className="composer-steer-btn"
              onClick={submitSteer}
              title="插队：立即注入当前正在运行的任务（不打断，agent 在下一步工具间隙即可看到）"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" />
              </svg>
              <span>插队</span>
            </button>
          )}

          {/* Send / Queue / Stop */}
          <button
            className={`composer-send-btn ${disabled || (busy && empty) ? "stop" : ""}`}
            onClick={submit}
            title={
              disabled
                ? "停止"
                : busy
                ? (!empty ? "排队发送" : "停止")
                : "发送"
            }
          >
            {disabled || (busy && empty) ? (
              <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
                <rect x="1" y="1" width="10" height="10" rx="2" />
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
