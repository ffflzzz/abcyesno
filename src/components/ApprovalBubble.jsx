import React, { useState } from "react";
import Icon from "./Icon.jsx";
import { toLoadableSrc } from "../utils/mediaSrc.js";
import { humanSummaryOf, friendlyOperationOf } from "../utils/approvalHuman.js";

function formatValue(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

/**
 * ApprovalBubble — inline chat-bubble style approval prompt.
 * Renders as an assistant message row so the user can review and respond
 * without leaving the conversation flow. Replaces the modal ApprovalDialog
 * for workflow HITL gates.
 */
export default function ApprovalBubble({ approval, onRespond, toolMessages = [], fallbackImages = [] }) {
  const [lightboxSrc, setLightboxSrc] = useState(null);
  const {
    operation,
    command,
    scope,
    args,
    tool_name,
    tool_call_id,
    description,
    source,
    label,
    message,
    artifacts,
    allowSteer,
    gateId,
    context,
    ended,
  } = approval || {};

  const [remember, setRemember] = useState(false);
  const [steerText, setSteerText] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [responding, setResponding] = useState(false);

  // data: URLs resolved from local file paths via the main-process IPC.
  // The sandboxed file:// renderer cannot load cross-directory file:// images,
  // so we read + base64-encode them in the main process (see read-local-image).
  const [resolved, setResolved] = useState({});
  const resolvedRef = React.useRef({});
  // Stable list of artifact keys that failed to resolve, to avoid re-calling.
  const failedRef = React.useRef({});

  // Extract a local filesystem path from an artifact descriptor, or null if the
  // artifact is a remote (http/data) URL that the renderer can load directly.
  function localPathOf(a) {
    if (a.path) return a.path;
    const s = a.src || a.url || "";
    if (/^file:\/\//i.test(s)) {
      let fp = s.replace(/^file:\/\//i, "");
      if (/^\/[A-Za-z]:/.test(fp)) fp = fp.slice(1);
      return fp;
    }
    if (/^(https?:|data:)/i.test(s)) return null; // remote — no IPC needed
    return s || null; // bare path
  }

  // Resolve every local-path artifact to a data URL once.
  React.useEffect(() => {
    let cancelled = false;
    (shownArtifacts || []).forEach((a, i) => {
      const key = a.id || a.label || `a${i}`;
      const lp = localPathOf(a);
      if (!lp) return; // remote URL, rendered directly
      if (resolvedRef.current[key] || failedRef.current[key]) return;
      const api = typeof window !== "undefined" && window.hermes;
      if (!api || !api.readLocalImage) return;
      api.readLocalImage(lp).then((r) => {
        if (cancelled) return;
        if (r && r.dataUrl) {
          resolvedRef.current[key] = r.dataUrl;
          setResolved((prev) => ({ ...prev, [key]: r.dataUrl }));
        } else {
          failedRef.current[key] = true;
        }
      }).catch(() => {
        if (!cancelled) failedRef.current[key] = true;
      });
    });
    return () => { cancelled = true; };
  }, [approval]);

  // 2026-08-30 可读性改造：弹窗主角改为「agent 想做什么」的人话摘要；
  // 原始命令默认折叠为技术详情（8 行/400 字以上必折叠）。
  const humanSummary = humanSummaryOf(approval);
  const operationName = friendlyOperationOf(approval) || "需要确认";
  const displayCommand = command
    || formatValue(args)
    || formatValue(description)
    || formatValue(context)
    || "";

  const shownArtifacts = Array.isArray(artifacts) ? artifacts : [];

  // 折叠规则：>8 行或 >400 字符一律默认折叠（纯字符数判断会放过 30 行短行命令）
  const lineCount = displayCommand ? displayCommand.split("\n").length : 0;
  const isLongContent = displayCommand.length > 400 || lineCount > 8;
  const COLLAPSED_CHARS = 360;
  const previewContent = isLongContent && !expanded
    ? displayCommand.slice(0, COLLAPSED_CHARS) + (displayCommand.length > COLLAPSED_CHARS ? "…" : "")
    : displayCommand;

  async function handleRespond(choice, withSteer) {
    if (responding) return;
    setResponding(true);
    try {
      await onRespond(choice, remember, withSteer ? steerText : "");
    } finally {
      setResponding(false);
    }
  }

  // Convert local file paths to abcyesno-local:// URLs so the sandboxed
  // renderer can display them (file:// cross-directory is blocked).
  function toImageUrl(a) {
    if (a.url) return toLoadableSrc(a.url);
    if (a.src) return toLoadableSrc(a.src);
    const p = a.path || "";
    if (!p) return null;
    return toLoadableSrc(p);
  }

  // Image-type artifacts for inline preview
  let imageArtifacts = shownArtifacts.filter((a) =>
    a.type === "image" || a.source === "url" || /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(a.url || a.path || a.label || "")
  );

  // Fallback: if no images in approval artifacts, extract from adjacent tool messages
  // (some backends put generated images in tool results rather than approval payload)
  if (imageArtifacts.length === 0 && toolMessages.length > 0) {
    const fallbackImages = [];
    const seenUrls = new Set();
    for (const m of toolMessages) {
      const content = m.result !== undefined ? m.result : m.content;
      if (!content) continue;
      // Scan for image URLs in tool result
      const imgRe = /(data:image\/[a-zA-Z0-9+]+;base64,[A-Za-z0-9+/=]{20,}|https?:\/\/[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp)[^\s"')]*|[A-Za-z]:\\[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp))/gi;
      let match;
      while ((match = imgRe.exec(content)) !== null) {
        const url = match[1];
        if (!seenUrls.has(url)) {
          seenUrls.add(url);
          fallbackImages.push({
            id: `tool-img-${fallbackImages.length}`,
            type: "image",
            url: url.startsWith("data:") || url.startsWith("http") ? url : null,
            path: url.match(/^[A-Za-z]:\\/i) ? url : null,
            src: url,
            label: `${m.toolName || "工具"} 产物`,
          });
        }
      }
      // Also check for image in structured result object
      if (typeof content === "object" && content !== null) {
        ["image", "image_url", "url", "path", "src", "preview", "frames"].forEach(k => {
          if (content[k]) {
            const val = String(content[k]);
            if (/^data:image\//i.test(val) || /^https?:\/\//i.test(val) || /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(val)) {
              if (!seenUrls.has(val)) {
                seenUrls.add(val);
                fallbackImages.push({
                  id: `tool-obj-${fallbackImages.length}`,
                  type: "image",
                  url: val,
                  label: `${m.toolName || "工具"} 产物`,
                });
              }
            }
          }
        });
      }
    }
    if (fallbackImages.length > 0) {
      imageArtifacts = fallbackImages;
    }
  }

  // Last resort: the caller (typically the workbench) may inject pre-collected
  // images that were emitted via workflow.artifact and already ingested on
  // the client side — even if the backend failed to attach them to the
  // approval payload (common when an upstream generator like Agnes 503'd).
  if (imageArtifacts.length === 0 && Array.isArray(fallbackImages) && fallbackImages.length > 0) {
    imageArtifacts = fallbackImages.map((a, i) => ({
      id: a.id || `fallback-${i}`,
      type: "image",
      url: a.url || a.src || a.path,
      path: a.path,
      src: a.src,
      label: a.label || "已收集的产物",
    }));
  }

  // 关键修复（2026-08-23）：审批任务已失效/已结束（后端 HITL 超时、或用户点
  // 确认时该 run 早已结束）。此时不能再渲染活的「批准」按钮——点了也是静默
  // 死锁（决策文件无人消费）。改为明确的失效态：禁用操作、给出原因、提供关闭。
  if (ended) {
    return (
      <div className="approval-pop approval-pop-ended">
        <div className="approval-pop-tail" aria-hidden="true" />
        <div className="approval-pop-card">
            <div className="approval-bubble-header">
              <span className="approval-bubble-icon"><Icon name="alert" size={14} /></span>
              <span className="approval-bubble-title">{label || "审批已失效"}</span>
              {operationName && operationName !== (label || "审批已失效") && (
                <span className="approval-bubble-gate">{operationName}</span>
              )}
            </div>
            <div className="approval-bubble-message">
              {message || "该审批任务已结束或已超时，无法再响应。请重新发起任务。"}
            </div>
          <div className="approval-bubble-actions">
            <button
              className="approval-btn dismiss"
              onClick={() => onRespond && onRespond(null, false, "")}
            >
              知道了
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="approval-pop">
      <div className="approval-pop-tail" aria-hidden="true" />
      <div className="approval-pop-card">
          {/* Header */}
          <div className="approval-bubble-header">
            <span className="approval-bubble-icon"><Icon name="warning" size={14} /></span>
            <span className="approval-bubble-title">需要确认</span>
            <span className="approval-bubble-gate">{operationName}</span>
          </div>

          {/* Message / description */}
          {message && (
            <div className="approval-bubble-message">{message}</div>
          )}

          {/* 2026-08-30 人话摘要：agent 想做什么（原始命令折叠在下方技术详情里） */}
          {humanSummary && (
            <div className="approval-bubble-message approval-bubble-human">
              <span className="approval-bubble-human-label">agent 想要</span>
              {humanSummary}
            </div>
          )}

          {/* Gate ID */}
          {gateId && (
            <div className="approval-bubble-meta">
              <span className="approval-bubble-meta-label">审批点</span>
              <span className="approval-bubble-meta-value">{gateId}</span>
            </div>
          )}

          {/* Image artifacts — inline thumbnail previews */}
          {imageArtifacts.length > 0 && (
            <div className="approval-bubble-artifacts">
              <div className="approval-bubble-artifacts-label">相关产物</div>
              <div className="approval-bubble-artifacts-grid">
                {imageArtifacts.map((a, i) => {
                  const key = a.id || a.label || `a${i}`;
                  const remote = (a.url && /^(https?:|data:)/i.test(a.url))
                    ? a.url
                    : (a.src && /^(https?:|data:)/i.test(a.src) ? a.src : null);
                  const src = remote || resolved[key];
                  if (src) {
                    return (
                      <img
                        key={key}
                        className="approval-bubble-artifact-thumb"
                        src={src}
                        alt={a.label || `产物${i + 1}`}
                        onClick={() => setLightboxSrc(src)}
                        style={{ cursor: "zoom-in" }}
                      />
                    );
                  }
                  const lp = localPathOf(a);
                  if (lp) {
                    return (
                      <div key={key} className="approval-bubble-artifact-thumb loading">
                        <span>读取中…</span>
                      </div>
                    );
                  }
                  return (
                    <span key={key} className="approval-bubble-artifact-chip">
                      {a.label || a.type || a.id}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          {/* Non-image artifact chips */}
          {shownArtifacts.length > imageArtifacts.length && (
            <div className="approval-bubble-artifacts">
              <div className="approval-bubble-artifacts-label">其他产物</div>
              <div className="approval-bubble-chips">
                {shownArtifacts
                  .filter((a) => !imageArtifacts.includes(a))
                  .map((a) => (
                    <span key={a.id || a.label} className="approval-bubble-chip">
                      {a.label || a.type || a.id}
                    </span>
                  ))}
              </div>
            </div>
          )}

          {/* Detailed content (collapsible when long) */}
          {displayCommand && (
            <div className={`approval-bubble-content ${isLongContent ? "collapsible" : ""} ${expanded ? "expanded" : ""}`}>
              <div className="approval-bubble-content-label">技术详情（原始命令）</div>
              <pre className="approval-bubble-code">{previewContent}</pre>
              {isLongContent && (
                <button
                  className="approval-bubble-expand-btn"
                  onClick={() => setExpanded((v) => !v)}
                >
                  {expanded ? (
                    <><Icon name="chevron" size={10} style={{ transform: "rotate(-90deg)" }} /> 收起</>
                  ) : (
                    <><Icon name="chevron" size={10} style={{ transform: "rotate(90deg)" }} /> 展开全部 ({displayCommand.length} 字符 / {lineCount} 行)</>
                  )}
                </button>
              )}
            </div>
          )}

          {/* Scope */}
          {scope && (
            <div className="approval-bubble-meta">
              <span className="approval-bubble-meta-label">影响范围</span>
              <span className="approval-bubble-meta-value">{scope}</span>
            </div>
          )}

          {/* Steer input (optional feedback) */}
          {allowSteer && (
            <div className="approval-bubble-steer">
              <div className="approval-bubble-steer-label">修改意见（可选）</div>
              <textarea
                className="approval-bubble-steer-input"
                value={steerText}
                placeholder="如需调整方向，填写修改意见后选择「带意见批准」"
                onChange={(e) => setSteerText(e.target.value)}
                rows={2}
              />
            </div>
          )}

          {/* Remember checkbox */}
          <label className="approval-bubble-remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>始终批准此类操作</span>
          </label>

          {/* Action buttons */}
          <div className="approval-bubble-actions">
            <button
              className="approval-btn reject"
              onClick={() => handleRespond(false, false)}
              disabled={responding}
            >
              拒绝
            </button>
            {allowSteer && (
              <button
                className="approval-btn steer"
                onClick={() => handleRespond(true, true)}
                disabled={responding}
              >
                带意见批准
              </button>
            )}
            <button
              className="approval-btn approve"
              onClick={() => handleRespond(true, false)}
              disabled={responding}
            >
              {responding ? "处理中…" : "批准"}
            </button>
          </div>
      </div>
      {/* Lightbox overlay */}
      {lightboxSrc && (
        <div className="approval-lightbox" onClick={() => setLightboxSrc(null)}>
          <img src={lightboxSrc} alt="放大预览" onClick={(e) => e.stopPropagation()} />
          <span className="approval-lightbox-close" onClick={() => setLightboxSrc(null)}>✕</span>
        </div>
      )}
    </div>
  );
}
