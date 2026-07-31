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

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  if (isToday) {
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

// ── Tab constants ──
const TABS = [
  { id: "chat", label: "对话", name: "chat" },
  { id: "workflow", label: "工作流", name: "workflow" },
  { id: "tasks", label: "任务", name: "tasks" },
];

export default function Sidebar({
  open,
  assistants,
  sessions,
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
  onOpenMarket,
  backendStatus,
  manifests = [],
  selectedWorkflowId,
  onSelectWorkflow,
  // ── Task panel props ──
  taskManager,           // { tasks, selectedTaskId, onSelectTask, createTask, stopTask, clearCompleted, clearAll }
}) {
  // Tab state (persisted per session)
  const [activeTab, setActiveTab] = useState(() => {
    try { return localStorage.getItem("abcyesno:sidebarTab") || "chat"; }
    catch (_) { return "chat"; }
  });
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

  // Map manifest icon to a unified Icon name
  const workflowIconName = (icon) => {
    switch (icon) {
      case "chat": return "chat";
      case "film": return "film";
      case "image": return "image";
      default: return "workflow";
    }
  };

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

  // ── Handle workflow run from sidebar → select & open dashboard ──
  function handleWorkflowRun(manifest) {
    // Select this workflow so ResultPanel shows its dashboard
    if (onSelectWorkflow) onSelectWorkflow(manifest.id);
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
              return (
                <div
                  key={s.id}
                  className={`session-item ${active ? "active" : ""}`}
                  onClick={() => onSelectSession(s.id)}
                  onContextMenu={(e) => openMenu(e, "session", s.id, s.title)}
                >
                  <div className="session-row">
                    <div className="session-title">{s.title}</div>
                    <div className="session-time">{formatTime(s.updatedAt)}</div>
                  </div>
                  <div className="session-preview">{s.preview || "无消息"}</div>
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

  /** Workflow tab: card grid */
  function renderWorkflowTab() {
    return (
      <div className="sidebar-tab-content">
        <div className="workflow-grid">
          {manifests.map((m) => {
            const active = m.id === selectedWorkflowId;
            return (
              <div
                key={m.id}
                className={`wf-card ${active ? "active" : ""}`}
                onClick={() => onSelectWorkflow && onSelectWorkflow(m.id)}
                title={m.description || m.name}
              >
                <div className="wf-card-icon"><Icon name={workflowIconName(m.icon)} size={20} /></div>
                <div className="wf-card-body">
                  <div className="wf-card-name">{m.name}</div>
                  <div className="wf-card-desc">{m.description || m.ui?.title || ""}</div>
                </div>
                {/* Run button — spawns background task */}
                {taskManager && (
                  <button
                    className="wf-card-run"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleWorkflowRun(m);
                    }}
                    title="后台运行此工作流"
                  >
                    <Icon name="play" size={14} /> 运行
                  </button>
                )}
              </div>
            );
          })}
          {manifests.length === 0 && (
            <div className="empty-hint">暂无工作流</div>
          )}
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
          {activeTab === "workflow" && renderWorkflowTab()}
          {activeTab === "tasks" && renderTasksTab()}
        </div>

        {/* Footer */}
        <div className="sidebar-footer">
          <button className="footer-btn" title="市场" onClick={onOpenMarket}><Icon name="market" size={14} /> 市场</button>
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
