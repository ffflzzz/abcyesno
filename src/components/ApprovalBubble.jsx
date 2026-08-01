import React, { useState } from "react";
import Icon from "./Icon.jsx";

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
export default function ApprovalBubble({ approval, onRespond, toolMessages = [] }) {
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

  const operationName = label || operation || tool_name || "未知操作";
  const displayCommand = command
    || formatValue(args)
    || formatValue(description)
    || formatValue(context)
    || "";

  const shownArtifacts = Array.isArray(artifacts) ? artifacts : [];

  // Truncate very long content for the bubble preview
  const isLongContent = displayCommand.length > 300;
  const previewContent = isLongContent && !expanded
    ? displayCommand.slice(0, 300) + "…"
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

  // Convert local file paths to file:// URLs so <img> can render them
  function toImageUrl(a) {
    if (a.url) return a.url;
    if (a.src && (a.src.startsWith("http") || a.src.startsWith("data:") || a.src.startsWith("file:"))) return a.src;
    const p = a.path || a.src || "";
    if (!p) return null;
    // Already a URL or data URI
    if (/^(https?:|data:|file:)/i.test(p)) return p;
    // Local filesystem path → file:// URL (normalize backslashes)
    const normalized = p.replace(/\\/g, "/");
    if (/^[A-Za-z]:/.test(normalized)) {
      return "file:///" + normalized;
    }
    return "file://" + normalized;
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

  return (
    <div className="message-row assistant">
      <div className="message-avatar agent-avatar approval">
        <Icon name="pause" size={18} style={{ color: "#d29922" }} />
      </div>
      <div className="message-col">
        <div className="message-bubble assistant approval-bubble">
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
              <pre className="approval-bubble-code">{previewContent}</pre>
              {isLongContent && (
                <button
                  className="approval-bubble-expand-btn"
                  onClick={() => setExpanded((v) => !v)}
                >
                  {expanded ? (
                    <><Icon name="chevron" size={10} style={{ transform: "rotate(-90deg)" }} /> 收起</>
                  ) : (
                    <><Icon name="chevron" size={10} style={{ transform: "rotate(90deg)" }} /> 展开全部 ({displayCommand.length} 字符)</>
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
