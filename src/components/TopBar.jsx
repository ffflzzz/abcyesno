import React from "react";
import TabBar from "./TabBar.jsx";

// Custom window title bar. Replaces the native OS chrome (Electron
// titleBarStyle: 'hidden'): the whole strip is a drag region, while the logo,
// the app TabBar (启动台 / AI 热点 / Excalidraw / +) and the window controls
// are individually marked no-drag (see .topbar-* styles). On macOS the system
// traffic lights occupy the top-left, so we skip the logo and our own controls
// and just leave a spacer.
export default function TopBar({
  tabs,
  activeTabId,
  onActivate,
  onClose,
  onAdd,
  onMinimize,
  onToggleMaximize,
  onCloseWindow,
}) {
  const isMac = (window.hermes && window.hermes.platform) === "darwin";

  return (
    <div className="topbar" onDoubleClick={onToggleMaximize}>
      <div className={`topbar-left${isMac ? " topbar-left--mac" : ""}`}>
        {!isMac && (
          <div className="topbar-brand">
            <span className="topbar-brand-mark" />
            <span className="topbar-brand-name">Abcyesno</span>
          </div>
        )}
      </div>

      <div className="topbar-tabs">
        <TabBar
          tabs={tabs}
          activeTabId={activeTabId}
          onActivate={onActivate}
          onClose={onClose}
          onAdd={onAdd}
        />
      </div>

      {!isMac && (
        <div className="topbar-controls">
          <button className="win-btn" onClick={onMinimize} title="最小化" aria-label="最小化">
            <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="4.5" width="8" height="1.4" fill="currentColor" /></svg>
          </button>
          <button className="win-btn" onClick={onToggleMaximize} title="最大化 / 还原" aria-label="最大化">
            <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1.2" /></svg>
          </button>
          <button className="win-btn win-btn--close" onClick={onCloseWindow} title="关闭" aria-label="关闭">
            <svg width="10" height="10" viewBox="0 0 10 10"><path d="M2 2L8 8M8 2L2 8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" /></svg>
          </button>
        </div>
      )}
    </div>
  );
}
