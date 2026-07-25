import React, { useState } from "react";

function formatValue(value) {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
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

  // Workflow (LangGraph HITL) approvals carry a human label; tool approvals
  // carry operation/command context. Render whichever is present.
  const operationName = label || operation || tool_name || '未知操作';
  const displayCommand = command
    || formatValue(args)
    || formatValue(description)
    || formatValue(approval);

  const shownArtifacts = Array.isArray(artifacts) ? artifacts : [];

  return (
    <div className="modal-mask">
      <div className="modal approval-modal">
        <h3>⚠️ 需要你的确认</h3>
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
          {shownArtifacts.length > 0 && (
            <div className="approval-row">
              <span className="approval-label">相关产物</span>
              <div className="approval-artifacts">
                {shownArtifacts.map((a) => (
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
