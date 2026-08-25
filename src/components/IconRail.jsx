import React from "react";
import Icon from "./Icon.jsx";

// Narrow vertical icon rail on the far left of the chat surface. Hosts the
// controls that used to live in the ChatLayout header's right cluster
// (result panel / browser panel / context usage / API key) plus a bottom
// sidebar collapse toggle. 48px wide, icons stack vertically, active state
// highlights like the old header did.
export default function IconRail({
  resultPanelOpen = false,
  onToggleResultPanel = () => {},
  onToggleResultPanelCollapse = () => {},
  resultPanelCollapsed = false,
  browserPanelOpen = false,
  onToggleBrowserPanel = () => {},
  onShowContextUsage = () => {},
  onOpenKey = () => {},
  sidebarOpen = true,
  onToggleSidebar = () => {},
}) {
  const resultActive = resultPanelOpen && !resultPanelCollapsed;

  function handleResultClick() {
    if (!resultPanelOpen) onToggleResultPanel();
    else onToggleResultPanelCollapse();
  }

  return (
    <nav className="icon-rail" aria-label="工具">
      <button
        className={`rail-btn ${resultActive ? "active" : ""}`}
        onClick={handleResultClick}
        title="结果区"
      >
        <Icon name="panel" size={18} />
      </button>
      <button
        className={`rail-btn ${browserPanelOpen ? "active" : ""}`}
        onClick={onToggleBrowserPanel}
        title="浏览器"
      >
        <Icon name="globe" size={18} />
      </button>
      <button
        className="rail-btn"
        onClick={onShowContextUsage}
        title="上下文用量"
      >
        <Icon name="activity" size={18} />
      </button>
      <button
        className="rail-btn"
        onClick={onOpenKey}
        title="设置 API Key"
      >
        <Icon name="settings" size={18} />
      </button>

      <div className="rail-spacer" />

      <button
        className="rail-btn"
        onClick={onToggleSidebar}
        title={sidebarOpen ? "收起侧边栏" : "展开侧边栏"}
      >
        <Icon name="sidebar" size={18} />
      </button>
    </nav>
  );
}
