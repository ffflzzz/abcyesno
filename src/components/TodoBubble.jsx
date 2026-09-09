import React, { useState } from "react";

/**
 * 待办气泡（docs/todos-panel.md §4）——消息流内嵌，跟随上下文逐步划线。
 *
 * 纯展示组件：输入 `items`（`{id, content, status}` 快照），不持有状态、
 * 不做 I/O。状态语义来自 Hermes 的 `todo` 工具：
 *   pending → in_progress → completed / cancelled
 * 每次工具调用携带的是**写后的完整列表**，所以这里整体渲染最新快照，
 * 不做增量合并（merge 语义由后端负责）。
 */
const STATUS_ICON = {
  pending: "○",
  in_progress: "◐",
  completed: "✅",
  cancelled: "–",
};

const COLLAPSE_THRESHOLD = 6;

export default function TodoBubble({ items = [] }) {
  const [expanded, setExpanded] = useState(false);
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) return null;

  const active = list.filter((t) => t.status !== "cancelled");
  const done = list.filter((t) => t.status === "completed").length;
  const total = active.length;
  const allDone = total > 0 && done === total;

  const collapsible = list.length > COLLAPSE_THRESHOLD;
  const visible = collapsible && !expanded ? list.slice(0, COLLAPSE_THRESHOLD) : list;
  const hiddenCount = list.length - visible.length;

  return (
    <div className={`todo-bubble ${allDone ? "all-done" : ""}`}>
      <div className="todo-head">
        <span className="todo-title">
          <span className="todo-icon" aria-hidden="true">📋</span>
          待办
        </span>
        <span className="todo-count">
          {done}/{total}
          {allDone && <span className="todo-all-done">全部完成</span>}
        </span>
        {collapsible && (
          <button
            className="todo-toggle"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "收起" : `展开其余 ${hiddenCount} 条`}
          >
            {expanded ? "收起" : `展开 ${hiddenCount}`}
          </button>
        )}
      </div>
      <ul className="todo-list">
        {visible.map((t) => (
          <li key={t.id} className={`todo-item ${t.status}`} title={t.content}>
            <span className="todo-mark" aria-hidden="true">
              {STATUS_ICON[t.status] || STATUS_ICON.pending}
            </span>
            <span className="todo-text">{t.content}</span>
          </li>
        ))}
      </ul>
      {collapsible && !expanded && hiddenCount > 0 && (
        <div className="todo-more">…另有 {hiddenCount} 条</div>
      )}
    </div>
  );
}
