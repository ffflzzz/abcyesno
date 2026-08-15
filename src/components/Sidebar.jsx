import React, { useState, useEffect, useRef, useMemo } from "react";
import DetailModal from "./DetailModal.jsx";
import TaskPanel from "./TaskPanel.jsx";
import bachAvatar from "../assets/bach-avatar.png";
import bachIcon from "../assets/bach-icon.png";
import Icon from "./Icon.jsx";

function getStatusDotClass(backendStatus) {
  if (!backendStatus) return "offline";
  if (!backendStatus.hermesReady) return "offline";
  if (!backendStatus.gatewayConnected) return "connecting";
  return "online";
}

function getInitials(name) {
  if (!name) return "?";
  return name.slice(0, 2).toUpperCase();
}

/** Strip markdown syntax and truncate for sidebar display. */
function stripMd(text, maxLen = 60) {
  if (!text || typeof text !== "string") return "";
  let s = text
    // Images: ![alt](url) → ""
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    // Links: [text](url) → text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    // Bold/italic: **text** / *text* / __text__ / _text_ → text
    .replace(/\*{1,3}([^*]+)\*{1,3}/g, "$1")
    .replace(/_{1,2}([^_]+)_{1,2}/g, "$1")
    // Inline code: `code` → code
    .replace(/`([^`]+)`/g, "$1")
    // Headings: # ## ### → ""
    .replace(/^#{1,6}\s+/gm, "")
    // Horizontal rules: --- / *** / ___ → ""
    .replace(/^[-*_]{3,}\s*$/gm, "")
    // Block quotes: > text → text
    .replace(/^>\s*/gm, "")
    // Clean up whitespace
    .replace(/\n{2,}/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
  if (s.length > maxLen) s = s.slice(0, maxLen - 1) + "…";
  return s;
}

function formatRelativeTime(ts) {
  if (!ts) return "";
  const now = new Date();
  const d = new Date(ts);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.floor((startOfToday - startOfDay) / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (diffDays === 2) return "前天";
  if (diffDays >= 3 && diffDays <= 7) return `${diffDays}天前`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function isDefaultTitle(title) {
  const t = (title || "").trim();
  return !t || t === "新会话";
}

/** Build a meaningful session title. Falls back to the first message preview
 *  when the backend never set a real title, so the list isn't all "新会话". */
function getSessionDisplayTitle(s) {
  const t = (s.title || "").trim();
  if (!isDefaultTitle(t)) return stripMd(t, 24);
  const fromPreview = stripMd(s.preview || "", 24);
  if (fromPreview && fromPreview !== "无消息") return fromPreview;
  return "新会话";
}

/** Sidebar subtitle: show the user's rename if present, otherwise nothing
 *  (we don't echo message previews here per UX request). */
function getSessionDisplayPreview(s) {
  const t = (s.title || "").trim();
  if (!isDefaultTitle(t)) return stripMd(t, 50);
  return "";
}

// ── Tab constants ──
const TABS = [
  { id: "chat", label: "对话", name: "chat" },
  { id: "tasks", label: "任务", name: "tasks" },
];

export default function Sidebar({
  open,
  assistants,
  sessions,
  runningSessionIds = [],
  selectedAssistantId,
  selectedSessionId,
  onSelectAssistant,
  onSelectSession,
  onCreateAssistant,
  onDeleteAssistant,
  onRenameAssistant,
  onNewSession,
  onDeleteSession,
  onRenameSession,
  onToggle,
  onOpenSkills,
  onOpenSettings,
  backendStatus,
  // ── Task panel props ──
  taskManager,           // { tasks, selectedTaskId, onSelectTask, createTask, stopTask, clearCompleted, clearAll }
  // Controlled tab (optional): when provided, App owns the tab state so other
  // surfaces (e.g. the chat-side AgentRunMonitor) can switch to "tasks".
  sidebarTab,
  onTabChange,
}) {
  // Tab state (persisted per session). Controlled when sidebarTab prop passed.
  const [internalTab, setInternalTab] = useState(() => {
    try { return localStorage.getItem("abcyesno:sidebarTab") || "chat"; }
    catch (_) { return "chat"; }
  });
  const activeTab = sidebarTab != null ? sidebarTab : internalTab;
  const setActiveTab = (t) => {
    if (onTabChange) onTabChange(t);
    else setInternalTab(t);
  };
  const [query, setQuery] = useState("");
  const [contextMenu, setContextMenu] = useState(null);
  const [detail, setDetail] = useState(null);
  const menuRef = useRef(null);
  const filtered = assistants.filter((a) => (a.name || "").toLowerCase().includes(query.toLowerCase()));
  const dotClass = getStatusDotClass(backendStatus);

  // Persist tab choice
  useEffect(() => {
    try { localStorage.setItem("abcyesno:sidebarTab", activeTab); } catch (_) {}
  }, [activeTab]);

  const assistantMap = useMemo(() => {
    const map = {};
    assistants.forEach((a) => { map[a.id] = a; });
    return map;
  }, [assistants]);

  useEffect(() => {
    if (!contextMenu) return;
    const handleClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setContextMenu(null);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [contextMenu]);

  function openMenu(e, type, id, name) {
    e.preventDefault();
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    setContextMenu({
      type, id, name,
      x: Math.min(e.clientX, rect.right - 10),
      y: e.clientY,
    });
  }

  function handleRename() {
    if (!contextMenu) return;
    const newName = window.prompt("重命名", contextMenu.name);
    if (newName && newName.trim() && newName.trim() !== contextMenu.name) {
      if (contextMenu.type === "assistant" && onRenameAssistant) {
        onRenameAssistant(contextMenu.id, newName.trim());
      } else if (contextMenu.type === "session" && onRenameSession) {
        onRenameSession(contextMenu.id, newName.trim());
      }
    }
    setContextMenu(null);
  }

  function handleDetail() {
    if (!contextMenu) return;
    if (contextMenu.type === "assistant") {
      const data = assistantMap[contextMenu.id];
      if (data) setDetail({ type: "assistant", data });
    } else if (contextMenu.type === "session") {
      const data = sessions.find((s) => s.id === contextMenu.id);
      if (data) setDetail({ type: "session", data });
    }
    setContextMenu(null);
  }

  function handleDelete() {
    if (!contextMenu) return;
    if (contextMenu.type === "assistant" && onDeleteAssistant) {
      onDeleteAssistant(contextMenu.id);
    } else if (contextMenu.type === "session" && onDeleteSession) {
      onDeleteSession(contextMenu.id);
    }
    setContextMenu(null);
  }

  // ── Render helpers for each tab ──

  /** Chat tab: session list only (no assistant switcher — single agent) */
  function renderChatTab() {
    return (
      <div className="sidebar-tab-content">
        {/* Session list (main content of chat tab) */}
        <div className="sidebar-sessions-area">
          <div className="sessions-header">
            <span className="section-label">会话</span>
            <button className="new-session-btn" onClick={() => onNewSession()} title="新建会话">
              + 新会话
            </button>
          </div>
          <div className="session-list">
            {sessions.map((s) => {
              const active = s.id === selectedSessionId;
              // Sessions keep streaming in the background after you switch
              // away — surface that so the list doesn't look frozen.
              const running = runningSessionIds.includes(s.id);
              return (
                <div
                  key={s.id}
                  className={`session-item ${active ? "active" : ""} ${running ? "running" : ""}`}
                  onClick={() => onSelectSession(s.id)}
                  onContextMenu={(e) => openMenu(e, "session", s.id, s.title)}
                >
                  <div className="session-row">
                    <div className="session-title">
                      {running && <span className="session-running-dot" title="正在生成" />}
                      {getSessionDisplayTitle(s)}
                    </div>
                    <div className="session-time">{formatRelativeTime(s.updatedAt)}</div>
                  </div>
                  {(() => {
                    const preview = getSessionDisplayPreview(s);
                    return preview ? (
                      <div className="session-preview" title={preview}>
                        {running ? "正在生成…" : preview}
                      </div>
                    ) : null;
                  })()}
                  <button
                    className="session-menu"
                    onClick={(e) => { e.stopPropagation(); onDeleteSession(s.id); }}
                    title="删除"
                  >
                    <Icon name="trash" size={12} />
                  </button>
                </div>
              );
            })}
            {sessions.length === 0 && (
              <div className="empty-hint">暂无会话，点击上方创建</div>
            )}
          </div>
        </div>
      </div>
    );
  }

  /** Tasks tab: TaskPanel */
  function renderTasksTab() {
    if (!taskManager) {
      return (
        <div className="sidebar-tab-content">
          <div className="task-empty-state">
            <div className="task-empty-text">任务管理器未就绪</div>
          </div>
        </div>
      );
    }

    return (
      <div className="sidebar-tab-content task-tab-content">
        <TaskPanel
          tasks={taskManager.tasks}
          selectedTaskId={taskManager.selectedTaskId}
          onSelectTask={taskManager.onSelectTask}
          onStopTask={taskManager.stopTask}
          onClearCompleted={taskManager.clearCompleted}
          onClearAll={taskManager.clearAll}
        />
      </div>
    );
  }

  // ── Main render ──
  return (
    <>
      {!open && (
        <button className="sidebar-toggle" onClick={onToggle} title="展开侧边栏">
          <Icon name="panel" size={16} />
        </button>
      )}
      <aside className={`sidebar ${open ? "open" : "closed"}`}>
        {/* Header */}
        <div className="sidebar-header">
          <div className="brand-small">
            <img src={bachIcon} alt="Abcyesno" className="logo-small" />
            <span className="brand-name">Abcyesno</span>
          </div>
          <button className="sidebar-close" onClick={onToggle}><Icon name="close" size={16} /></button>
        </div>

        {/* Tab bar (replaces old search bar) */}
        <div className="sidebar-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`sidebar-tab ${activeTab === t.id ? "active" : ""}`}
              onClick={() => setActiveTab(t.id)}
            >
              <span className="tab-icon"><Icon name={t.name} size={14} /></span>
              <span className="tab-label">{t.label}</span>
              {t.id === "tasks" && taskManager && taskManager.tasks.filter((tk) => tk.status === "running").length > 0 && (
                <span className="tab-badge">
                  {taskManager.tasks.filter((tk) => tk.status === "running").length}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Tab content area */}
        <div className="sidebar-body">
          {activeTab === "chat" && renderChatTab()}
          {activeTab === "tasks" && renderTasksTab()}
        </div>

        {/* Footer */}
        <div className="sidebar-footer">
          <button className="footer-btn" title="技能" onClick={onOpenSkills}><Icon name="skills" size={14} /> 技能</button>
          <button className="footer-btn" title="设置" onClick={onOpenSettings}><Icon name="settings" size={14} /> 设置</button>
        </div>

        {/* Context menu */}
        {contextMenu && (
          <div
            ref={menuRef}
            className="context-menu"
            style={{ top: contextMenu.y, left: contextMenu.x }}
          >
            <div className="context-menu-item" onClick={handleDetail}>查看详情</div>
            <div className="context-menu-item" onClick={handleRename}>重命名</div>
            <div className="context-menu-item danger" onClick={handleDelete}>删除</div>
          </div>
        )}
        {detail && (
          <DetailModal
            type={detail.type}
            data={detail.data}
            onClose={() => setDetail(null)}
          />
        )}
      </aside>
    </>
  );
}
