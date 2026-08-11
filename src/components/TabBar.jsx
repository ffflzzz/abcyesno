import React from "react";
import Icon from "./Icon.jsx";

// Browser-style tab strip. Each tab is a pill (icon + label + close ×); a +
// button at the end opens a fresh homepage tab. Tabs with duplicate titles get
// a numeric suffix (对话 1 / 对话 2) so they stay distinguishable — exactly
// like a browser where you can have several tabs on the same site open.
export default function TabBar({ tabs, activeTabId, onActivate, onClose, onAdd }) {
  const totals = {};
  for (const t of tabs) {
    const base = t.title || "标签";
    totals[base] = (totals[base] || 0) + 1;
  }

  let seen = {};
  return (
    <div className="tabbar">
      <div className="tabbar-tabs">
        {tabs.map((tab) => {
          const base = tab.title || "标签";
          seen[base] = (seen[base] || 0) + 1;
          // Homepage tabs are always just "启动台"; other duplicate titles
          // (e.g. several 对话 tabs) keep a numeric suffix to stay distinct.
          const label = tab.type === "homepage" || totals[base] <= 1 ? base : `${base} ${seen[base]}`;
          const active = tab.id === activeTabId;
          return (
            <div
              key={tab.id}
              className={`tabbar-tab${active ? " active" : ""}`}
              onClick={() => onActivate(tab.id)}
              title={base}
            >
              <span className="tabbar-tab-icon"><Icon name={tab.icon || "home"} size={15} /></span>
              <span className="tabbar-tab-label">{label}</span>
              {tabs.length > 1 && (
                <button
                  className="tabbar-tab-close"
                  onClick={(e) => { e.stopPropagation(); onClose(tab.id); }}
                  title="关闭标签"
                >
                  <Icon name="x" size={13} />
                </button>
              )}
            </div>
          );
        })}
        <button className="tabbar-add" onClick={onAdd} title="新建标签页">
          <Icon name="plus" size={16} />
        </button>
      </div>
    </div>
  );
}
