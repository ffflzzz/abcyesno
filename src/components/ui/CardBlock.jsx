import React from "react";
import UiMarkdown from "./markdown.jsx";

/**
 * CardBlock — 信息卡片 (spec §5.3)
 * props: { title, icon?, body(markdown), actions?:[{label}], tone?:'default'|'info'|'warn'|'success' }
 * MVP：actions 仅展示，不接 tool（spec §7）。
 */
const TONE_CLASS = {
  default: "",
  info: "ui-card-info",
  warn: "ui-card-warn",
  success: "ui-card-success",
};

export default function CardBlock({ title, icon, body, actions = [], tone = "default" }) {
  if (!title && !body) return null;
  const toneCls = TONE_CLASS[tone] || "";
  return (
    <div className={`ui-block ui-card ${toneCls}`}>
      <div className="ui-card-head">
        {icon && <span className="ui-card-icon">{icon}</span>}
        {title && <span className="ui-card-title">{title}</span>}
      </div>
      {body && (
        <div className="ui-card-body">
          <UiMarkdown>{body}</UiMarkdown>
        </div>
      )}
      {Array.isArray(actions) && actions.length > 0 && (
        <div className="ui-card-actions">
          {actions.map((a, i) => (
            <button key={i} className="ui-card-action" type="button" disabled>
              {a?.label || a}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
