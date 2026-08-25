import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { useTts } from "../hooks/useTts.jsx";
import { stripMarkdownToText } from "../utils/stripMarkdown.js";

/**
 * MessageActions — ChatGPT-style toolbar under each chat bubble.
 *
 * Features:
 *   1. Copy           — clipboard.writeText(cleaned markdown text)
 *   2. Like / Dislike  — toggle (mutually exclusive), persisted per-message to localStorage
 *   3. TTS Read-aloud  — window.speechSynthesis with running state
 *   4. Regenerate      — for assistant: re-run with the previous user prompt
 *   5. Share          — navigator.share() if available, else fallback to clipboard
 *   6. More menu      — Edit / Delete / Copy raw markdown / Report
 *   7. Right-end footer — model name + relative timestamp (assistant only)
 *
 * Default mode: invisible. Becomes visible on bubble hover (handled in CSS by
 * `.message-row:hover .message-actions` and `.message-actions:focus-within`).
 * For touch devices and the most recent message we always show.
 */

const RATINGS_KEY = "abc:msg-ratings";

function loadRatings() {
  try {
    const raw = localStorage.getItem(RATINGS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}
function saveRatings(map) {
  try {
    localStorage.setItem(RATINGS_KEY, JSON.stringify(map));
  } catch {
    /* noop */
  }
}

function formatRelative(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const hh = d.getHours().toString().padStart(2, "0");
  const mm = d.getMinutes().toString().padStart(2, "0");
  if (sameDay) return `今天 ${hh}:${mm}`;
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  const isYest =
    d.getFullYear() === yest.getFullYear() &&
    d.getMonth() === yest.getMonth() &&
    d.getDate() === yest.getDate();
  if (isYest) return `昨天 ${hh}:${mm}`;
  // older — show MM-DD HH:MM
  const M = (d.getMonth() + 1).toString().padStart(2, "0");
  const D = d.getDate().toString().padStart(2, "0");
  return `${M}-${D} ${hh}:${mm}`;
}

export default function MessageActions({
  message,
  isUser,
  cleanedText,
  rawText,
  assistant,
  alwaysShow = false,
  onRegenerate,
  onRetry,
  onEdit,
  onDelete,
}) {
  const [ratings, setRatings] = useState(() => loadRatings());
  const [copied, setCopied] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [toast, setToast] = useState("");
  const moreRef = useRef(null);
  const copyTimerRef = useRef(null);
  const toastTimerRef = useRef(null);

  // Global TTS controller — single <audio> in TtsProvider, so a message bubble
  // unmounting (Virtuoso virtual list) must NOT cancel playback. State lives in
  // the provider; this component only reflects highlight via currentMsgId.
  const { speak, stop, isPlaying, currentMsgId } = useTts();

  // Close "more" on outside click
  useEffect(() => {
    if (!moreOpen) return;
    const onDocClick = (e) => {
      if (moreRef.current && !moreRef.current.contains(e.target)) {
        setMoreOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [moreOpen]);

  const showToast = (msg) => {
    setToast(msg);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(""), 1800);
  };

  const myRating = message?.id ? ratings[message.id] : null; // "up" | "down" | null

  const setRating = (which) => {
    if (!message?.id) return;
    setRatings((prev) => {
      const cur = prev[message.id];
      const nextVal = cur === which ? null : which;
      const next = { ...prev };
      if (nextVal == null) delete next[message.id];
      else next[message.id] = nextVal;
      saveRatings(next);
      return next;
    });
    showToast(which === "up" ? "已点赞" : "已标记不满意");
  };

  const handleCopy = async () => {
    const text = stripMarkdownToText(cleanedText || rawText || "");
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // fallback
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 1400);
      showToast("已复制");
    } catch (err) {
      console.error("copy failed", err);
      showToast("复制失败");
    }
  };

  const handleSpeak = () => {
    const isThisSpeaking = isPlaying && currentMsgId === message?.id;
    if (isThisSpeaking) {
      stop();
      return;
    }
    const text = stripMarkdownToText(cleanedText || rawText || "");
    if (!text) {
      showToast("没有可朗读的内容");
      return;
    }
    if (!window.hermes || !window.hermes.synthesizeSpeech) {
      showToast("当前环境不支持朗读");
      return;
    }
    speak(text, message?.id);
  };

  const handleShare = async () => {
    const text = stripMarkdownToText(cleanedText || rawText || "");
    if (navigator.share) {
      try {
        await navigator.share({ title: "ABC 分享", text });
        return;
      } catch (err) {
        if (err?.name === "AbortError") return;
        // fall through to clipboard
      }
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        showToast("已复制到剪贴板（可粘贴分享）");
      } else {
        showToast("当前环境不支持分享");
      }
    } catch {
      showToast("分享失败");
    }
  };

  const handleCopyRaw = async () => {
    const text = rawText || cleanedText || "";
    try {
      await navigator.clipboard.writeText(text);
      showToast("已复制原始内容");
    } catch {
      showToast("复制失败");
    }
    setMoreOpen(false);
  };

  const handleReport = () => {
    setMoreOpen(false);
    // We don't have a backend report endpoint; just persist locally and acknowledge.
    try {
      const list = JSON.parse(localStorage.getItem("abc:msg-reports") || "[]");
      list.push({ id: message?.id, ts: Date.now() });
      localStorage.setItem("abc:msg-reports", JSON.stringify(list));
    } catch {
      /* noop */
    }
    showToast("已记录反馈，谢谢");
  };

  const handleEdit = () => {
    setMoreOpen(false);
    if (onEdit) onEdit(message);
  };

  const handleDelete = () => {
    setMoreOpen(false);
    if (onDelete) onDelete(message);
  };

  const cls = `message-actions ${alwaysShow ? "always-show" : ""} ${
    isUser ? "actions-user" : "actions-assistant"
  }`;

  return (
    <div className={cls} role="toolbar" aria-label="消息操作">
      {/* ── Left: action icons ── */}
      <div className="msg-actions-left">
        {isUser && onRetry && (
          <button
            className="msg-action-btn"
            onClick={() => onRetry(message)}
            title="重试这条用户消息"
            aria-label="重试"
          >
            <Icon name="refresh" size={15} />
          </button>
        )}
        {!isUser && (
          <>
            <button
              className={`msg-action-btn ${copied ? "is-active copied" : ""}`}
              onClick={handleCopy}
              title={copied ? "已复制" : "复制"}
              aria-label="复制"
            >
              {copied ? <Icon name="check" size={15} /> : <Icon name="copy" size={15} />}
            </button>

            <button
              className={`msg-action-btn ${myRating === "up" ? "is-active liked" : ""}`}
              onClick={() => setRating("up")}
              title="点赞"
              aria-label="点赞"
            >
              <Icon name="thumbs-up" size={15} />
            </button>

            <button
              className={`msg-action-btn ${myRating === "down" ? "is-active disliked" : ""}`}
              onClick={() => setRating("down")}
              title="不满意"
              aria-label="不满意"
            >
              <Icon name="thumbs-down" size={15} />
            </button>

            <button
              className={`msg-action-btn ${isPlaying && currentMsgId === message?.id ? "is-active speaking" : ""}`}
              onClick={handleSpeak}
              title={isPlaying && currentMsgId === message?.id ? "停止朗读" : "朗读"}
              aria-label={isPlaying && currentMsgId === message?.id ? "停止朗读" : "朗读"}
            >
              <Icon name={isPlaying && currentMsgId === message?.id ? "stop-circle" : "audio"} size={15} />
            </button>

            {onRegenerate && (
              <button
                className="msg-action-btn"
                onClick={onRegenerate}
                title="重新生成"
                aria-label="重新生成"
              >
                <Icon name="refresh-cw" size={15} />
              </button>
            )}

            <button
              className="msg-action-btn"
              onClick={handleShare}
              title="分享"
              aria-label="分享"
            >
              <Icon name="share" size={15} />
            </button>
          </>
        )}

        <div className="msg-actions-more-wrap" ref={moreRef}>
          <button
            className="msg-action-btn"
            onClick={() => setMoreOpen((v) => !v)}
            title="更多"
            aria-label="更多"
            aria-expanded={moreOpen}
          >
            <Icon name="more" size={15} />
          </button>
          {moreOpen && (
            <div className="msg-actions-popover" role="menu">
              {isUser && onEdit && (
                <button className="pop-item" onClick={handleEdit} role="menuitem">
                  <Icon name="pen" size={13} />
                  <span>编辑消息</span>
                </button>
              )}
              <button className="pop-item" onClick={handleCopyRaw} role="menuitem">
                <Icon name="copy" size={13} />
                <span>复制原始内容</span>
              </button>
              {!isUser && (
                <button className="pop-item" onClick={handleReport} role="menuitem">
                  <Icon name="alert" size={13} />
                  <span>反馈问题</span>
                </button>
              )}
              {onDelete && (
                <>
                  <div className="pop-divider" />
                  <button
                    className="pop-item pop-item-danger"
                    onClick={handleDelete}
                    role="menuitem"
                  >
                    <Icon name="trash" size={13} />
                    <span>删除消息</span>
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Right: model name + timestamp (assistant only) ── */}
      {!isUser && (
        <div className="msg-actions-right">
          <span className="msg-meta-model" title={assistant?.name || "ABC"}>
            <Icon name="bot" size={11} />
            <span>{assistant?.name || "ABC"}</span>
          </span>
          <span className="msg-meta-time">{formatRelative(message?.createdAt)}</span>
        </div>
      )}

      {toast && <span className="msg-actions-toast" key={toast}>{toast}</span>}
    </div>
  );
}
