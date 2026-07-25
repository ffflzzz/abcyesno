import React, { useState, useRef, useEffect } from "react";
import bachPeek from "../assets/bach-peek.png";

const AGNES_MODELS = [
  { id: "agnes-2.0-flash", name: "agnes-2.0-flash", tag: "快" },
  { id: "agnes-2.0-pro", name: "agnes-2.0-pro", tag: "强" },
  { id: "__custom__", name: "自定义模型...", tag: "" },
];

// Only two modes are real in Hermes: default (backend "ask") and yolo
// (session approval bypass). "strict" (ask on every call) has no native
// backend support, so it is intentionally omitted — no fake switch.
const PERMISSION_MODES = [
  { id: "default", name: "默认权限", desc: "工具按后端默认策略自动批准" },
  { id: "yolo", name: "完全自动", desc: "本会话不再询问，直接执行" },
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
// Images live inline in the editable surface as contentEditable=false <img>
// nodes at the caret position. On send we serialize: text → newlines, each
// <img> → a positional placeholder [[IMG:i]]; the i-th image's dataUrl is
// stashed in imagesRef. ChatLayout restores [[IMG:i]] to a markdown image so
// the model sees the picture exactly where the user placed it.

function makeInlineImage(dataUrl, idx) {
  const img = document.createElement("img");
  img.src = dataUrl;
  img.setAttribute("data-inline-img", String(idx));
  img.setAttribute("contentEditable", "false");
  img.className = "composer-inline-img";
  return img;
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
      if (tag === "IMG") {
        out += `[[IMG:${node.getAttribute("data-inline-img")}]]`;
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
  disabled,
  placeholder,
  attachment,
  onClearAttachment,
  permission,
  onPermissionChange,
  busy = false,
  queuedMessages = [],
  onRemoveQueued,
  mentionables = [],
}) {
  const [empty, setEmpty] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const [sending, setSending] = useState(false);
  const [customModel, setCustomModel] = useState("");
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [showCustomModel, setShowCustomModel] = useState(false);
  const [showPermissionMenu, setShowPermissionMenu] = useState(false);
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

  // Close all popovers on outside click
  useEffect(() => {
    if (!showModelMenu && !showPermissionMenu && !showPlusMenu && !showCustomModel && !mentionOpen) return;
    const handleClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setShowModelMenu(false);
        setShowPermissionMenu(false);
        setShowPlusMenu(false);
        setShowCustomModel(false);
        setMentionOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showModelMenu, showPermissionMenu, showPlusMenu, showCustomModel]);

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
  };

  const submit = () => {
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
    Promise.resolve(onSend(content, images, mentions)).catch((err) => {
      console.error("send failed", err);
    }).finally(() => {
      setSending(false);
    });
  };

  // Insert an image inline at the caret; stash its dataUrl for send.
  const insertImageInline = (dataUrl) => {
    const el = editableRef.current;
    if (!el) return;
    el.focus();
    const idx = imagesRef.current.length;
    imagesRef.current.push(dataUrl);
    insertNodeAtCaret(el, makeInlineImage(dataUrl, idx));
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
            insertImageInline(dataUrl);
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
        insertImageInline(dataUrl);
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
      return model || "agnes-2.0-flash";
    }
    return model || "agnes-2.0-flash";
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
      {/* ── Bach peeking over the composer ── */}
      <a
        className="composer-bach-peek"
        href="https://abcyesno.cn"
        target="_blank"
        rel="noopener noreferrer"
        title={busy ? "巴赫正在工作中… 点击访问官网" : "巴赫在等你~ 点击访问官网"}
      >
        <img src={bachPeek} alt="Bach" draggable={false} />
      </a>
      {/* ── File attachment chip (non-image files still go through upload) ── */}
      {attachment && attachment.type !== "image" && (
        <div className="composer-attachment-row">
          <div className="composer-attachment">
            <span className="attachment-file-icon">📎</span>
            <span className="attachment-name" title={attachment.fileName}>{attachment.fileName}</span>
            <button className="attachment-clear" onClick={onClearAttachment} title="移除附件">
              ✕
            </button>
          </div>
        </div>
      )}

      {/* ── Queued messages (typed while agent is busy) ── */}
      {queuedMessages.length > 0 && (
        <div className="composer-queue">
          {queuedMessages.map((m) => (
            <div key={m.id} className="composer-queue-item">
              <span className="composer-queue-icon" title="排队中">⏳</span>
              <span className="composer-queue-text">{m.text}</span>
              <button
                className="composer-queue-remove"
                onClick={() => onRemoveQueued && onRemoveQueued(m.id)}
                title="移除"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ── Top: rich input area (text + inline images) ── */}
      <div className="composer-editable-wrap">
        {empty && (
          <div className="composer-placeholder">
            {placeholder || "今天帮你做些什么？ @ 引用对话文件，/ 调用技能与指令"}
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
                <span className={`mention-kind ${m.kind}`}>{m.kind === "workflow" ? "⚙" : "💬"}</span>
                <span className="mention-name">{m.name}</span>
                <span className="mention-id">{m.id}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Voice error / status line ── */}
      {voiceError && (
        <div className="composer-voice-error" onClick={() => setVoiceError("")} title="点击关闭">
          ⚠ {voiceError}
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
                  <span className="menu-icon">＋</span> 新会话
                </button>
                <button className="composer-menu-item" onClick={() => { setShowPlusMenu(false); onUpload && onUpload(); }} disabled={disabled}>
                  <span className="menu-icon">📎</span> 上传文件
                </button>
                <button className="composer-menu-item" onClick={() => { setShowPlusMenu(false); onShowSkills && onShowSkills(); }} disabled={disabled}>
                  <span className="menu-icon">⚡</span> 技能与工作流
                </button>
              </div>
            )}
          </div>

          {/* Permission selector */}
          <div className="composer-menu-wrap">
            <button
              className="composer-pill-btn"
              title="权限模式"
              onClick={() => { setShowPermissionMenu(!showPermissionMenu); setShowPlusMenu(false); setShowModelMenu(false); }}
            >
              <span className="pill-icon">🛡</span>
              <span className="pill-label">{currentPermission.name}</span>
              <span className="pill-caret">▾</span>
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
                    <span className="menu-icon">{p.id === permission ? "●" : "○"}</span>
                    <span>
                      <div className="menu-item-title">{p.name}</div>
                      <div className="menu-item-desc">{p.desc}</div>
                    </span>
                  </button>
                ))}
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
              onClick={() => { setShowModelMenu(!showModelMenu); setShowPlusMenu(false); setShowPermissionMenu(false); }}
              disabled={disabled}
            >
              <span className="pill-icon">⊕</span>
              <span className="pill-label">{currentModelLabel()}</span>
              <span className="pill-caret">▾</span>
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
                    <span className="menu-icon">{m.id === model ? "●" : "○"}</span>
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
            {recording ? "⏹" : "🎤"}
          </button>

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
