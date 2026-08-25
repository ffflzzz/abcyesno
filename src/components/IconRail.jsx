import React from "react";
import Icon from "./Icon.jsx";

// Narrow vertical icon rail on the far left of the chat surface. Hosts the
// controls that used to live in the ChatLayout header's right cluster
// (result panel / browser panel / context usage) and the Sidebar footer
// (skills / wechat / settings), plus a bottom sidebar collapse toggle.
// 48px wide, icons stack vertically, active state highlights like the
// old header did.
function getWechatDotClass(ws) {
  if (!ws) return "offline";
  if (ws.state === "connected") return "online";
  if (["awaiting_qr", "qr_expired", "connecting", "reconnecting"].includes(ws.state)) return "connecting";
  return "offline";
}

export default function IconRail({
  resultPanelOpen = false,
  onToggleResultPanel = () => {},
  onToggleResultPanelCollapse = () => {},
  resultPanelCollapsed = false,
  browserPanelOpen = false,
  onToggleBrowserPanel = () => {},
  onShowContextUsage = () => {},
  onOpenSkills = () => {},
  onOpenWechatBind = () => {},
  wechatStatus = { state: "idle", bound: false },
  ttsMute = false,
  onToggleTtsMute = () => {},
  ttsIsPlaying = false,
  ttsCanPlay = false,
  onToggleTtsPlay = () => {},
  onOpenSettings = () => {},
  sidebarOpen = true,
  onToggleSidebar = () => {},
}) {
  const resultActive = resultPanelOpen && !resultPanelCollapsed;
  const wechatDot = getWechatDotClass(wechatStatus);

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
        onClick={onOpenSkills}
        title="技能"
      >
        <Icon name="skills" size={18} />
      </button>
      <button
        className="rail-btn"
        onClick={onOpenWechatBind}
        title="微信绑定"
      >
        <Icon name="wechat" size={18} />
        <span className={`rail-status-dot ${wechatDot}`} />
      </button>
      <button
        className={`rail-btn ${ttsIsPlaying ? "active" : ""}`}
        onClick={onToggleTtsPlay}
        title={ttsIsPlaying ? "停止朗读" : "朗读最新回复"}
        aria-label={ttsIsPlaying ? "停止朗读" : "朗读最新回复"}
        disabled={!ttsIsPlaying && !ttsCanPlay}
        style={{
          opacity: (!ttsIsPlaying && !ttsCanPlay) ? 0.4 : 1,
          cursor: (!ttsIsPlaying && !ttsCanPlay) ? "default" : "pointer",
        }}
      >
        <Icon name={ttsIsPlaying ? "stop-circle" : "play"} size={18} />
      </button>
      <button
        className={`rail-btn ${ttsMute ? "active" : ""}`}
        onClick={onToggleTtsMute}
        title={ttsMute ? "取消静音" : "静音（自动朗读开启时生效）"}
        aria-label={ttsMute ? "取消静音" : "静音"}
        style={{ color: ttsMute ? "var(--accent)" : undefined }}
      >
        <Icon name={ttsMute ? "volume-x" : "audio"} size={18} />
      </button>

      <div className="rail-spacer" />

      <button
        className="rail-btn"
        onClick={onOpenSettings}
        title="设置"
      >
        <Icon name="settings" size={18} />
      </button>
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
