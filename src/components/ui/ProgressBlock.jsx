import React from "react";

/**
 * ProgressBlock — 步骤进度条 (spec §5.4)
 * props: { steps:[{label,status:'done'|'active'|'pending'|'error'}], current? }
 * 通用组件（非 workflow 事件驱动），视觉与 WorkflowTimeline 一致。
 */
const MAX_STEPS = 50;

const STEP_CLASS = {
  done: "ui-step-done",
  active: "ui-step-active",
  pending: "ui-step-pending",
  error: "ui-step-error",
};

const STEP_ICON = {
  done: "✓",
  active: "●",
  pending: "○",
  error: "✕",
};

export default function ProgressBlock({ steps = [], current }) {
  const safeSteps = Array.isArray(steps) ? steps.slice(0, MAX_STEPS) : [];
  if (safeSteps.length === 0) return null;
  return (
    <div className="ui-block ui-progress">
      <ol className="ui-progress-list">
        {safeSteps.map((s, i) => {
          const status = s?.status || (current != null && i < current ? "done" : "pending");
          const cls = STEP_CLASS[status] || "ui-step-pending";
          return (
            <li key={i} className={`ui-step ${cls}`}>
              <span className="ui-step-dot" aria-hidden>
                {STEP_ICON[status] || "○"}
              </span>
              <span className="ui-step-label">{s?.label || `步骤 ${i + 1}`}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
