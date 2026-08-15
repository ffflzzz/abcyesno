import React, { useState } from "react";
import Icon from "./Icon.jsx";

function formatValue(value) {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function localPathOf(a) {
  if (a.path) return a.path;
  const s = a.src || a.url || "";
  if (/^file:\/\//i.test(s)) {
    let fp = s.replace(/^file:\/\//i, "");
    if (/^\/[A-Za-z]:/.test(fp)) fp = fp.slice(1);
    return fp;
  }
  if (/^(https?:|data:)/i.test(s)) return null;
  return s || null;
}

export default function ApprovalDialog({ approval, onRespond }) {
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
  } = approval || {};

  const [remember, setRemember] = useState(false);
  const [steerText, setSteerText] = useState("");
  const [resolved, setResolved] = useState({});
  const resolvedRef = React.useRef({});
  const failedRef = React.useRef({});

  // Workflow (LangGraph HITL) approvals carry a human label; tool approvals
  // carry operation/command context. Render whichever is present.
  const operationName = label || operation || tool_name || '未知操作';
  const displayCommand = command
    || formatValue(args)
    || formatValue(description)
    || formatValue(approval);

  const shownArtifacts = Array.isArray(artifacts) ? artifacts : [];

  // Resolve local image artifacts via main-process IPC (file:// cannot be
  // loaded directly in the sandboxed renderer).
  React.useEffect(() => {
    let cancelled = false;
    imageArtifacts.forEach((a, i) => {
      const key = a.id || a.label || `a${i}`;
      const lp = localPathOf(a);
      if (!lp) return;
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

  const imageArtifacts = shownArtifacts.filter((a) =>
    a.type === "image" || a.source === "url" || /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(a.url || a.path || a.label || "")
  );

  return (
    <div className="modal-mask">
      <div className="modal approval-modal">
        <h3><Icon name="warning" size={16} /> 需要你的确认</h3>
        <p className="modal-desc">以下操作需要批准后才能继续执行：</p>
        <div className="approval-detail">
          <div className="approval-row">
            <span className="approval-label">操作类型</span>
            <span className="approval-value">{operationName}</span>
          </div>
          {gateId && (
            <div className="approval-row">
              <span className="approval-label">审批点</span>
              <span className="approval-value">{gateId}</span>
            </div>
          )}
          {tool_call_id && (
            <div className="approval-row">
              <span className="approval-label">工具调用 ID</span>
              <span className="approval-value">{tool_call_id}</span>
            </div>
          )}
          {message && (
            <div className="approval-row">
              <span className="approval-label">说明</span>
              <span className="approval-value">{message}</span>
            </div>
          )}
          <div className="approval-row">
            <span className="approval-label">具体内容</span>
            <pre className="approval-code">{displayCommand}</pre>
          </div>
          {imageArtifacts.length > 0 && (
            <div className="approval-row">
              <span className="approval-label">相关产物</span>
              <div className="approval-artifacts">
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
                        className="approval-artifact-thumb"
                        src={src}
                        alt={a.label || `产物${i + 1}`}
                        style={{ maxWidth: 120, maxHeight: 120, borderRadius: 6, cursor: "zoom-in" }}
                      />
                    );
                  }
                  return (
                    <span className="approval-artifact-chip" key={key}>
                      {a.label || a.type || a.id}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          {shownArtifacts.length > imageArtifacts.length && (
            <div className="approval-row">
              <span className="approval-label">其他产物</span>
              <div className="approval-artifacts">
                {shownArtifacts
                  .filter((a) => !imageArtifacts.includes(a))
                  .map((a) => (
                    <span className="approval-artifact-chip" key={a.id || a.label}>
                      {a.label || a.type || a.id}
                    </span>
                  ))}
              </div>
            </div>
          )}
          {scope && (
            <div className="approval-row">
              <span className="approval-label">影响范围</span>
              <span className="approval-value">{scope}</span>
            </div>
          )}
          {allowSteer && (
            <div className="approval-row approval-steer">
              <span className="approval-label">修改意见（可选）</span>
              <textarea
                className="approval-steer-input"
                value={steerText}
                placeholder="如需调整方向，填写修改意见后选择「带意见批准」"
                onChange={(e) => setSteerText(e.target.value)}
              />
            </div>
          )}
          <label className="approval-remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <span>始终批准此类操作</span>
          </label>
        </div>
        <div className="modal-actions">
          <button className="ghost" onClick={() => onRespond(false, remember)}>拒绝</button>
          {allowSteer && (
            <button
              className="secondary"
              onClick={() => onRespond(true, remember, steerText)}
            >
              带意见批准
            </button>
          )}
          <button className="primary" onClick={() => onRespond(true, remember)}>批准</button>
        </div>
      </div>
    </div>
  );
}
