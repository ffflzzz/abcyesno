import React, { useEffect, useMemo, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import ChatLayout from "./components/ChatLayout.jsx";
import ApiKeyModal from "./components/ApiKeyModal.jsx";
import MarketPanel from "./components/MarketPanel.jsx";
import CreateAssistantModal from "./components/CreateAssistantModal.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";
import ResultPanel from "./components/ResultPanel.jsx";
import ConfirmModal from "./components/ConfirmModal.jsx";
import { initContract, listManifests } from "./contract/registry.js";
import { subscribeContractEvents } from "./contract/eventBus.js";
import { sanitizeMessageContent } from "./components/MessageThread.jsx";
import { useAgentStream } from "./hooks/useAgentStream.js";
import { useTaskManager } from "./components/TaskPanel.jsx";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, stack: null, showDetails: false };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    if (window.hermes && window.hermes.logError) {
      window.hermes.logError(error && error.message ? error.message : String(error));
    }
    console.error(error, info);
    this.setState({ stack: info && info.componentStack ? info.componentStack : null });
  }
  render() {
    if (this.state.hasError) {
      const { showDetails } = this.state;
      return (
        <div className="error-fallback">
          <div className="error-card">
            <div className="error-emoji">⚡</div>
            <h2 className="error-title">界面遇到点小问题</h2>
            <p className="error-subtitle">这通常不影响已保存的对话数据</p>

            <button className="error-reload-btn" onClick={() => window.location.reload()}>
              <span className="error-reload-icon">↻</span> 重新加载
            </button>

            <button
              className="error-details-toggle"
              onClick={() => this.setState(s => ({ showDetails: !s.showDetails }))}
            >
              {showDetails ? '收起详情' : '查看技术详情'}
              <span className={`error-toggle-arrow ${showDetails ? 'open' : ''}`}>▸</span>
            </button>

            {showDetails && (
              <div className="error-details">
                <div className="error-msg-block">
                  <span className="error-label">错误信息</span>
                  <pre className="error-pre">{this.state.error && (this.state.error.message || String(this.state.error))}</pre>
                </div>
                {this.state.stack && (
                  <div className="error-stack-block">
                    <span className="error-label">组件路径</span>
                    <pre className="error-pre error-stack-pre">{this.state.stack}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Isolated ErrorBoundary for ResultPanel (sidebar crashes must never blank the main chat) ──
class ResultPanelErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    console.error("[ResultPanelErrorBoundary]", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <aside className="result-panel" style={{ width: 320, flexShrink: 0, borderLeft: '1px solid var(--border)', background: 'var(--panel)', padding: 16, overflow: 'auto' }}>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>结果区出错</div>
          <pre style={{ fontSize: 11, color: 'var(--error-text)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {this.state.error && (this.state.error.message || String(this.state.error))}
          </pre>
          <button className="primary" style={{ marginTop: 10 }} onClick={() => window.location.reload()}>刷新</button>
        </aside>
      );
    }
    return this.props.children;
  }
}

function normalizeContent(content) {
  if (typeof content === "string") return content;
  if (content === null || content === undefined) return "";
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && part.type === "text") return part.text || "";
        return JSON.stringify(part);
      })
      .join("");
  }
  return JSON.stringify(content);
}

// ── 纯数据消息转换（脱离 CopilotKit）─────────────────────────
function toStorageMessage(m) {
  return {
    id: m.id || String(Date.now()),
    role: m.role,
    content: sanitizeMessageContent(normalizeContent(m.content)),
    createdAt: m.createdAt || Date.now(),
    toolName: m.toolName,
    args: m.args,
    result: m.result,
    status: m.status,
    durationMs: m.durationMs,
    isError: m.isError,
  };
}

function ChatShell({
  assistant,
  session,
  selectedAssistantId,
  selectedSessionId,
  assistants,
  sessions,
  version,
  sidebarOpen,
  setSidebarOpen,
  showKeyModal,
  setShowKeyModal,
  showSettings,
  setShowSettings,
  approval,
  onRespondApproval,
  onNewSession,
  onDeleteSession,
  onRenameSession,
  onCreateAssistant,
  onDeleteAssistant,
  onRenameAssistant,
  onSelectAssistant,
  onSelectSession,
  onSessionUpdated,
  model,
  onModelChange,
  hermes,
  backendStatus,
  skills,
  showSkills,
  onToggleSkills,
  showMarket,
  setShowMarket,
  runError,
  onClearRunError,
  onApiKeySaved,
  manifests,
  selectedWorkflowId,
  onSelectWorkflow,
  aguiPort,
  resultPanelOpen = false,
  onToggleResultPanel = () => {},
  onOpenPreviewUrl,
  externalPreviewUrl = null,
  setExternalPreviewUrl,
  resultPanelCollapsed = false,
  onToggleResultPanelCollapse,
  resultPanelWidth = 380,
  setResultPanelWidth,
}) {
  const {
    messages: visibleMessages,
    phase,
    thinkingText,
    error: streamError,
    isStreaming,
    uiBlocks,
    stalled,
    sendMessage,
    stop,
    reset,
    setHistory,
  } = useAgentStream(aguiPort);

  // Permission mode: default (backend "ask") or yolo (session approval bypass).

  // Permission mode: default (backend "ask") or yolo (session approval bypass).
  // Pushed to the backend via the gateway; fails silently if not connected.
  const [permissionMode, setPermissionMode] = useState("default");
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null); // { message } | null
  // Queue of user messages typed while the agent is busy. Flushed FIFO when
  // the current run finishes (see effect below).
  const [queuedMessages, setQueuedMessages] = useState([]);
  async function handlePermissionChange(mode) {
    setPermissionMode(mode);
    if (!selectedSessionId || !hermes || !hermes.setPermissionMode) return;
    try {
      await hermes.setPermissionMode(mode, selectedSessionId);
    } catch (err) {
      console.error("set permission mode failed", err);
    }
  }

  const pendingSendRef = useRef(null);
  const wasLoadingRef = useRef(false);
  // Track whether a session was actively streaming so we don't kill a
  // just-started first-send stream when selecting a newly-created session.
  const hadActiveSessionRef = useRef(false);

  // Queued messages belong to a session; drop them when switching away.
  useEffect(() => {
    setQueuedMessages([]);
  }, [selectedSessionId]);
  const historyInitializedRef = useRef(false);
  const latestMessagesRef = useRef([]);

  // Reset history loader whenever the active session changes.
  useEffect(() => {
    historyInitializedRef.current = false;
  }, [selectedSessionId]);

  // Load persisted messages when the session changes.
  // IMPORTANT: We must abort any ongoing stream from the PREVIOUS session
  // before switching — otherwise stale SSE events leak into the new session.
  // BUT: when switching from "no session" to a freshly-created one (first send),
  // there is no previous stream to abort. Calling stop() here would kill the
  // SSE that handleSend just started (race condition: setState → effect fires
  // → stop() aborts the in-flight fetch). Guard with hadActiveSessionRef.
  useEffect(() => {
    if (historyInitializedRef.current) return;
    // Only abort if we had an active session before (not "" → first send).
    if (hadActiveSessionRef.current) {
      stop();
    }
    reset();
    const stored = session?.messages || [];
    if (stored.length === 0) {
      historyInitializedRef.current = true;
      setHistory([]);
      return;
    }
    setHistory(stored.map((m) => ({ ...m })));
    historyInitializedRef.current = true;
    // Update tracking: now we have an active session.
    hadActiveSessionRef.current = !!selectedSessionId;
  }, [selectedSessionId, session, setHistory, stop, reset]);

  async function doSend(text, mentions, explicitThreadId) {
    if (!text.trim()) return;
    const threadId = explicitThreadId || selectedSessionId;
    const history = visibleMessages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ id: m.id, role: m.role, content: m.content }));
    await sendMessage(text, {
      threadId,
      assistantId: selectedAssistantId,
      skillId: assistant?.skillId,
      model,
      history,
      mentions,
    });
  }

  // Persist messages and update session title/preview after a run finishes.
  useEffect(() => {
    if (wasLoadingRef.current && !isStreaming && selectedSessionId && hermes) {
      const uiMessages = visibleMessages.map(toStorageMessage);
      const userMsg = uiMessages.find((m) => m.role === "user");
      const assistantMsg = [...uiMessages].reverse().find((m) => m.role === "assistant");
      const patch = { messages: uiMessages };
      if (userMsg && session?.title === "新会话") {
        patch.title = (userMsg.content || "").slice(0, 24).replace(/\n/g, " ") || "新会话";
      }
      if (assistantMsg) {
        const rawPreview = assistantMsg.content || "";
        const clean = sanitizeMessageContent(rawPreview);
        patch.preview = clean.slice(0, 45).replace(/\n/g, " ") || "(新对话)";
      }
      hermes.updateSession(selectedSessionId, patch).then(() => {
        if (onSessionUpdated) onSessionUpdated();
      }).catch((err) => {
        console.error("session save failed", err);
      });
    }
    wasLoadingRef.current = isStreaming;
  }, [isStreaming, selectedSessionId, visibleMessages, hermes, session]);

  // Track the latest messages every render so we can flush them on switch/unmount.
  latestMessagesRef.current = visibleMessages;

  // Flush the current session's messages when switching away or unmounting.
  useEffect(() => {
    const sid = selectedSessionId;
    return () => {
      const msgs = latestMessagesRef.current;
      if (sid && msgs && msgs.length > 0 && hermes) {
        const ui = msgs.map(toStorageMessage);
        hermes.updateSession(sid, { messages: ui }).catch((err) => {
          console.error("session flush on switch failed", err);
        });
      }
    };
  }, [selectedSessionId, hermes]);

  // Auto-send a message that was queued while creating a new session.
  useEffect(() => {
    if (pendingSendRef.current !== null && selectedSessionId) {
      const { text, mentions } = pendingSendRef.current;
      pendingSendRef.current = null;
      doSend(text, mentions).catch((err) => {
        console.error("send failed", err);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId]);

  async function handleSend(text, mentions) {
    if (!text.trim()) return;
    // No active session → create one inline, then immediately send.
    // IMPORTANT: We do NOT call loadSessions here because it triggers
    // setSessions → re-render → ChatShell remount (via key=selectedSessionId)
    // which destroys the active useAgentStream instance and causes TDZ/blank.
    // The session list refreshes naturally via existing effects (post-send
    // persistence callback + onSessionUpdated).
    if (!selectedSessionId) {
      try {
        const assistantId = selectedAssistantId || "default";
        const session = await hermes.createSession(assistantId, "新会话");
        // Select the new session (triggers history-reset effect inside ChatShell,
        // but does NOT remount it because we removed the key prop).
        onSelectSession(session.id);
        // Send immediately using the explicit session id — don't wait for
        // React to flush state. The stream hooks pick up the new threadId
        // via the closure-free explicitThreadId parameter.
        await doSend(text, mentions, session.id);
        return;
      } catch (err) {
        console.error("auto-create session failed", err);
        return;
      }
    }
    // Busy → queue instead of dropping; the composer stays editable.
    if (isStreaming) {
      setQueuedMessages((q) => [...q, { id: `q-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, text, mentions }]);
      return;
    }
    await doSend(text, mentions);
  }

  function handleRemoveQueued(id) {
    setQueuedMessages((q) => q.filter((m) => m.id !== id));
  }

  // ── Task manager: long-chain workflow tasks run independently in sidebar ──
  const taskManager = useTaskManager(
    (text) => doSend(text),
    () => { handleStop(); }
  );

  // Flush the queue FIFO once the current run finishes.
  useEffect(() => {
    if (!isStreaming && queuedMessages.length > 0 && selectedSessionId) {
      const [next, ...rest] = queuedMessages;
      setQueuedMessages(rest);
      doSend(next.text, next.mentions).catch((err) => console.error("queued send failed", err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming, queuedMessages, selectedSessionId]);

  async function handleStop() {
    stop();
    // Stop also abandons anything the user queued behind the current run.
    setQueuedMessages([]);
    // Also interrupt the Hermes session directly so queued/running turns abort.
    if (selectedSessionId && window.hermes && window.hermes.interruptSession) {
      try {
        await window.hermes.interruptSession(selectedSessionId);
      } catch (err) {
        console.error("direct interrupt failed", err);
      }
    }
  }

  async function handleRetry(message) {
    if (!message?.content) return;
    await doSend(message.content);
  }

  function handleRegenerate() {
    const lastAssistantIndex = [...visibleMessages]
      .map((m) => m.role)
      .lastIndexOf("assistant");
    if (lastAssistantIndex === -1) return;
    const userBefore = [...visibleMessages]
      .slice(0, lastAssistantIndex)
      .map((m) => m.role)
      .lastIndexOf("user");
    if (userBefore === -1) return;
    const userMsg = visibleMessages[userBefore];
    setHistory(visibleMessages.slice(0, lastAssistantIndex));
    doSend(normalizeContent(userMsg.content));
  }

  // ── Message edit / delete (MessageActions toolbar) ──────────────────────
  function handleEditMessage(message) {
    if (isStreaming) return;
    setEditingMessageId(message.id);
  }

  function handleCancelEdit() {
    setEditingMessageId(null);
  }

  async function handleSaveEdit(messageId, newContent) {
    if (isStreaming || !newContent.trim()) return;
    const idx = visibleMessages.findIndex((m) => m.id === messageId);
    if (idx === -1) return;
    // Truncate everything after the edited message, then re-send with new text.
    // This mirrors ChatGPT: editing a user message discards the old reply and
    // regenerates a fresh one.
    const truncated = visibleMessages.slice(0, idx);
    setHistory(truncated);
    setEditingMessageId(null);
    await doSend(newContent);
  }

  function handleDeleteMessage(message) {
    if (isStreaming) return;
    setDeleteConfirm({ message });
  }

  async function confirmDeleteMessage() {
    const message = deleteConfirm?.message;
    if (!message) return;
    setDeleteConfirm(null);
    const newMsgs = visibleMessages.filter((m) => m.id !== message.id);
    setHistory(newMsgs);
    // Persist immediately — deletion doesn't trigger a streaming cycle so the
    // auto-save effect (which fires on isStreaming false→true→false) won't run.
    if (selectedSessionId && hermes) {
      const ui = newMsgs.map(toStorageMessage);
      try {
        await hermes.updateSession(selectedSessionId, { messages: ui });
        if (onSessionUpdated) onSessionUpdated();
      } catch (err) {
        console.error("delete message persist failed", err);
      }
    }
  }

  // Combine prop runError with stream error for the banner.
  const displayError = runError || streamError;

  return (
    <>
      <Sidebar
        open={sidebarOpen}
        assistants={assistants}
        sessions={sessions}
        selectedAssistantId={selectedAssistantId}
        selectedSessionId={selectedSessionId}
        onSelectAssistant={onSelectAssistant}
        onSelectSession={onSelectSession}
        onCreateAssistant={onCreateAssistant}
        onDeleteAssistant={onDeleteAssistant}
        onRenameAssistant={onRenameAssistant}
        onNewSession={onNewSession}
        onDeleteSession={onDeleteSession}
        onRenameSession={onRenameSession}
        onToggle={() => setSidebarOpen((o) => !o)}
        onOpenSkills={onToggleSkills}
        onOpenSettings={() => setShowSettings(true)}
        onOpenMarket={() => setShowMarket(true)}
        backendStatus={backendStatus}
        manifests={manifests}
        selectedWorkflowId={selectedWorkflowId}
        onSelectWorkflow={onSelectWorkflow}
        taskManager={taskManager}
      />
      <ChatLayout
        assistant={assistant}
        session={session}
        assistants={assistants}
        messages={visibleMessages}
        status={isStreaming ? "thinking" : "ready"}
        streamPhase={phase}
        thinkingText={thinkingText}
        uiBlocks={uiBlocks}
        stalled={stalled}
        version={version}
        sidebarOpen={sidebarOpen}
        model={model}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
        onNewSession={onNewSession}
        onSend={handleSend}
        onStop={handleStop}
        onOpenKey={() => setShowKeyModal(true)}
        onModelChange={onModelChange}
        permission={permissionMode}
        onPermissionChange={handlePermissionChange}
        queuedMessages={queuedMessages}
        onRemoveQueued={handleRemoveQueued}
        backendStatus={backendStatus}
        skills={skills}
        runError={displayError}
        onClearRunError={onClearRunError}
        manifests={manifests}
        selectedWorkflowId={selectedWorkflowId}
        onSelectWorkflow={onSelectWorkflow}
        approval={approval}
        onRespondApproval={onRespondApproval}
        showSkills={showSkills}
        onToggleSkills={onToggleSkills}
        onRetry={handleRetry}
        onRegenerate={handleRegenerate}
        resultPanelOpen={resultPanelOpen}
        onToggleResultPanel={onToggleResultPanel}
        onOpenPreviewUrl={onOpenPreviewUrl}
        resultPanelCollapsed={resultPanelCollapsed}
        onToggleResultPanelCollapse={onToggleResultPanelCollapse}
        selectedSessionId={selectedSessionId}
        onEditMessage={handleEditMessage}
        onDeleteMessage={handleDeleteMessage}
        editingMessageId={editingMessageId}
        onSaveEdit={handleSaveEdit}
        onCancelEdit={handleCancelEdit}
      />
      {/* ResultPanel wrapped in own ErrorBoundary so a sidebar crash never kills the main chat */}
      <ResultPanelErrorBoundary>
      {resultPanelOpen && (
        <div className="result-panel-wrapper">
        <div
          className="result-panel-resize-handle"
          onMouseDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const startX = e.clientX;
            const startW = resultPanelWidth;
            // Use a ref-captured updater so we always read the latest width for localStorage
            let currentW = startW;
            const onMove = (ev) => {
              ev.preventDefault();
              ev.stopPropagation();
              const delta = startX - ev.clientX;
              currentW = Math.min(Math.max(startW + delta, 220), window.innerWidth * 0.78);
              setResultPanelWidth(currentW);
            };
            const onUp = () => {
              try { localStorage.setItem('abc:resultPanelWidth', String(currentW)); } catch {}
              document.removeEventListener('mousemove', onMove);
              document.removeEventListener('mouseup', onUp);
              document.body.style.cursor = '';
              document.body.style.userSelect = '';
              document.body.style.removeProperty('--resizing');
            };
            document.addEventListener('mousemove', onMove, { passive: false });
            document.addEventListener('mouseup', onUp, { passive: true });
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            document.body.style.setProperty('--resizing', '1');
          }}
        />
      <ResultPanel
        sessionId={selectedSessionId}
        aguiPort={aguiPort}
        selectedWorkflowId={selectedWorkflowId}
        manifests={manifests}
        session={session}
        onSend={handleSend}
        onStop={handleStop}
        model={model}
        backendStatus={backendStatus}
        onSelectWorkflow={onSelectWorkflow}
        externalPreviewUrl={externalPreviewUrl}
        onClearExternalPreview={() => setExternalPreviewUrl(null)}
        collapsed={resultPanelCollapsed}
        onToggleCollapse={onToggleResultPanelCollapse}
        style={!resultPanelCollapsed ? { width: resultPanelWidth, minWidth: resultPanelWidth, flexShrink: 0 } : undefined}
      />
        </div>
      )}
      </ResultPanelErrorBoundary>
      {showKeyModal && (
        <ApiKeyModal onSave={async (key) => { await hermes.setApiKey(key); if (onApiKeySaved) onApiKeySaved(key); setShowKeyModal(false); }} onClose={() => setShowKeyModal(false)} />
      )}
      {showMarket && (
        <MarketPanel
          skills={skills}
          enabledSkills={enabledSkills}
          manifests={manifests}
          onToggleSkill={handleToggleSkill}
          onClose={() => setShowMarket(false)}
        />
      )}
      <ConfirmModal
        open={!!deleteConfirm}
        title="删除消息"
        message="确定删除这条消息？删除后无法恢复。"
        danger={true}
        onConfirm={confirmDeleteMessage}
        onClose={() => setDeleteConfirm(null)}
      />
    </>
  );
}

export default function App({ aguiPort }) {
  const [assistants, setAssistants] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [selectedAssistantId, setSelectedAssistantId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [model, setModel] = useState("agnes-2.5-flash");
  const [apiKey, setApiKey] = useState("");
  const [apiKeySet, setApiKeySet] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [showMarket, setShowMarket] = useState(false);
  const [version, setVersion] = useState("");
  const [approval, setApproval] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [resultPanelOpen, setResultPanelOpen] = useState(false);
  const [resultPanelCollapsed, setResultPanelCollapsed] = useState(false);
  const [resultPanelWidth, setResultPanelWidth] = useState(() => {
    try { return Number(localStorage.getItem('abc:resultPanelWidth')) || 380; } catch { return 380; }
  });
  const [externalPreviewUrl, setExternalPreviewUrl] = useState(null); // URL or "tab:xxx" to switch sidebar tab (e.g. abcyesno.cn / tab:artifacts)
  const [confirmDialog, setConfirmDialog] = useState(null); // { title, message, danger, onConfirm } | null
  const [skills, setSkills] = useState([{ id: "default", name: "通用助手", category: "general" }]);
  const [backendStatus, setBackendStatus] = useState(
    aguiPort ? { hermesReady: true, gatewayConnected: true } : { hermesReady: false, gatewayConnected: false }
  );
  const [runError, setRunError] = useState(null);
  const [enabledSkills, setEnabledSkills] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("abcyesno:enabledSkills") || "{}");
    } catch (_) {
      return {};
    }
  });
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("abcyesno:theme") || "dark";
    } catch (_) {
      return "dark";
    }
  });
  const hermes = window.hermes;

  // Contract layer: manifests drive the generic rendering components, and
  // selectedWorkflowId remembers which contract workflow (if any) is active in
  // the composer. Adding a workflow never touches this file beyond the data.
  const [manifests, setManifests] = useState(() => listManifests());
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");

  // Apply the active theme to the document root. theme can be "dark", "light",
  // or "system" (follow prefers-color-scheme). For "system" we also listen for
  // OS changes so the UI flips without a reload.
  useEffect(() => {
    function apply() {
      const root = document.documentElement;
      if (theme === "light") {
        root.setAttribute("data-theme", "light");
      } else if (theme === "dark") {
        root.setAttribute("data-theme", "dark");
      } else {
        const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        root.setAttribute("data-theme", prefersDark ? "dark" : "light");
      }
    }
    apply();
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    if (!hermes) return;
    hermes.getVersion().then(setVersion);
    hermes.getApiKeyStatus().then((ok) => {
      setApiKeySet(!!ok);
      setShowKeyModal(!ok);
    });
    loadAssistants();
    hermes.listSkills().then((list) => {
      if (list && list.length > 0) {
        setSkills([{ id: "default", name: "通用助手", category: "general" }, ...list]);
      }
    });

    async function autoApprove(req) {
      try {
        await hermes.respondApproval(req.id, true);
      } catch (err) {
        console.error("auto approval failed", err);
        setApproval(req);
      }
    }

    const onApproval = (req) => {
      const allowed = JSON.parse(localStorage.getItem("abcyesno:allowedOps") || "[]");
      if (req && req.operation && allowed.includes(req.operation)) {
        autoApprove(req);
        return;
      }
      setApproval(req);
    };
    hermes.on("approval-request", onApproval);

    const onGatewayStatus = (status) => {
      setBackendStatus((prev) => ({ ...prev, gatewayConnected: !!status.connected }));
    };
    hermes.on("gateway-status", onGatewayStatus);

    hermes.getStatus().then((status) => {
      // Only downgrade if we started as not-ready; never flip from ready→not-ready
      // after Bootstrap has already confirmed the backend is up (aguiPort != null).
      setBackendStatus(prev => ({
        hermesReady: prev.hermesReady || !!status.hermesReady,
        gatewayConnected: prev.gatewayConnected || !!status.gatewayConnected,
      }));
    });

    // Pull contract manifests from the adapter (falls back to bundled set).
    initContract(aguiPort).then(setManifests);

    // L4: a workflow.approval event carries a gate context for the existing
    // ApprovalDialog. Reuse the dialog; the workflow brake routes through the
    // file-based control channel instead of the Hermes tool-approval gateway.
    const onContractEvent = (runId, ev) => {
      const type = ev && (ev.type || ev.name) || "";
      if (type.endsWith("approval")) {
        const p = ev.payload || {};
        if (p.workflowRunId) {
          setApproval({
            id: p.gate_id || "workflow-approval",
            operation: p.gate_id || "workflow-approval",
            source: "workflow",
            runId: p.workflowRunId,
            gateId: p.gate_id,
            label: p.label,
            message: p.message || p.label || "工作流需要确认",
            artifacts: p.artifacts || [],
            allowSteer: !!p.allowSteer,
            context: p.context,
          });
        } else {
          setApproval({
            id: p.gate_id || "contract-approval",
            operation: p.gate_id || "contract-approval",
            context: p.context,
            message: p.label || p.gate_id || "工作流需要确认",
          });
        }
      }
    };
    const unsubContract = subscribeContractEvents(onContractEvent);

    return () => {
      hermes.off("approval-request", onApproval);
      hermes.off("gateway-status", onGatewayStatus);
      unsubContract && unsubContract();
    };
  }, [hermes]);

  useEffect(() => {
    if (!selectedAssistantId) return;
    loadSessions(selectedAssistantId);
  }, [selectedAssistantId]);

  useEffect(() => {
    const assistant = assistants.find((a) => a.id === selectedAssistantId);
    if (assistant?.defaultModel) {
      setModel(assistant.defaultModel);
    }
  }, [selectedAssistantId, assistants]);

  async function loadAssistants() {
    const list = await hermes.listAssistants();
    setAssistants(list || []);
    if (list && list.length > 0 && !selectedAssistantId) {
      setSelectedAssistantId(list[0].id);
    }
  }

  async function loadSessions(assistantId) {
    let list = await hermes.listSessions(assistantId);
    // Filter out empty sessions (never had any message) and clean them from storage.
    const emptyIds = (list || []).filter((s) => !s.messages || s.messages.length === 0).map((s) => s.id);
    if (emptyIds.length > 0) {
      // Clean empty sessions in background (fire-and-forget)
      for (const id of emptyIds) {
        hermes.deleteSession(id).catch(() => {});
      }
      list = (list || []).filter((s) => s.messages && s.messages.length > 0);
    }
    // Most-recently-active first so old sessions stay visible instead of
    // sinking below the fold as new ones are appended at the tail.
    list = (list || []).slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    setSessions(list);
    // Don't auto-select last session on startup — user gets a clean slate
    // and can pick history from sidebar manually.
    if (!selectedSessionId) {
      setSelectedSessionId("");
    }
  }

  /** Session switch handler (streaming guard lives in ChatShell where isStreaming is in scope). */
  function handleSelectSession(id) {
    if (id === selectedSessionId) return;
    setSelectedSessionId(id);
  }

  async function handleCreateAssistant(data) {
    await hermes.createAssistant({
      name: data.name,
      skillId: data.skillId || "default",
      avatar: data.avatar || "",
      description: data.skillId && data.skillId !== "default"
        ? `基于 ${data.skillId} skill`
        : "默认助手",
    });
    setShowCreateModal(false);
    await loadAssistants();
  }

  async function handleDeleteAssistant(id) {
    setConfirmDialog({
      title: "删除助手",
      message: "确定删除这个助手？删除后无法恢复。",
      danger: true,
      onConfirm: async () => {
        await hermes.deleteAssistant(id);
        if (selectedAssistantId === id) {
          setSelectedAssistantId("");
          setSelectedSessionId("");
        }
        await loadAssistants();
      },
    });
  }

  async function handleRenameAssistant(id, name) {
    try {
      await hermes.updateAssistant(id, { name });
      await loadAssistants();
    } catch (err) {
      console.error("rename assistant failed", err);
      window.alert("重命名失败：" + (err.message || String(err)));
    }
  }

  async function handleNewSession(title) {
    // Lazy creation: don't persist until user sends first message.
    // Just clear selection to give a blank slate. The real session gets
    // created inline inside handleSend() when !selectedSessionId.
    setSelectedSessionId("");
  }

  async function handleDeleteSession(id) {
    setConfirmDialog({
      title: "删除会话",
      message: "确定删除这个会话？对话记录将被永久删除。",
      danger: true,
      onConfirm: async () => {
        await hermes.deleteSession(id);
        if (selectedSessionId === id) {
          // Deleting current session while streaming → switch to empty (aborts stream)
          setSelectedSessionId("");
        }
        await loadSessions(selectedAssistantId);
      },
    });
  }

  async function handleRenameSession(id, title) {
    try {
      await hermes.updateSession(id, { title });
      await loadSessions(selectedAssistantId);
    } catch (err) {
      console.error("rename session failed", err);
      window.alert("重命名失败：" + (err.message || String(err)));
    }
  }

  async function handleModelChange(newModel) {
    setModel(newModel);
    const assistant = assistants.find((a) => a.id === selectedAssistantId);
    if (selectedAssistantId && assistant && newModel !== assistant.defaultModel) {
      try {
        await hermes.updateAssistant(selectedAssistantId, { defaultModel: newModel });
        await loadAssistants();
      } catch (err) {
        console.error("update assistant model failed", err);
      }
    }
  }

  // Skill-market enable/disable is persisted client-side so it survives reloads.
  function handleToggleSkill(id) {
    setEnabledSkills((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      try {
        localStorage.setItem("abcyesno:enabledSkills", JSON.stringify(next));
      } catch (_) {}
      return next;
    });
  }

  function handleThemeChange(nextTheme) {
    setTheme(nextTheme);
    try {
      localStorage.setItem("abcyesno:theme", nextTheme);
    } catch (_) {}
  }

  async function handleApprove(choice, remember, steerText) {
    if (!approval) return;
    try {
      if (approval.source === "workflow") {
        // LangGraph HITL brake: write the decision to the control channel so
        // the paused graph (blocked at interrupt()) can resume. "带意见批准"
        // (steer) carries non-empty steerText -> send decision "steer" so the
        // runtime applies the notes; plain approve sends "approve".
        const hasSteer = !!(choice && steerText && steerText.trim());
        await hermes.sendWorkflowInterrupt({
          workflowRunId: approval.runId,
          decision: choice ? (hasSteer ? "steer" : "approve") : "reject",
          steerText: hasSteer ? steerText : "",
        });
      } else {
        await hermes.respondApproval(approval.id, choice);
        if (remember && approval.operation) {
          const allowed = JSON.parse(localStorage.getItem("abcyesno:allowedOps") || "[]");
          if (!allowed.includes(approval.operation)) {
            allowed.push(approval.operation);
            localStorage.setItem("abcyesno:allowedOps", JSON.stringify(allowed));
          }
        }
      }
    } catch (err) {
      console.error("approval response failed", err);
    }
    setApproval(null);
  }

  const assistant = useMemo(
    () => assistants.find((a) => a.id === selectedAssistantId),
    [assistants, selectedAssistantId]
  );
  const session = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId),
    [sessions, selectedSessionId]
  );

  return (
    <ErrorBoundary>
      <div className="app">
        <ChatShell
          assistant={assistant}
          session={session}
          selectedAssistantId={selectedAssistantId}
          selectedSessionId={selectedSessionId}
          assistants={assistants}
          sessions={sessions}
          version={version}
          sidebarOpen={sidebarOpen}
          setSidebarOpen={setSidebarOpen}
          showKeyModal={showKeyModal}
          setShowKeyModal={setShowKeyModal}
          showSettings={showSettings}
          setShowSettings={setShowSettings}
          approval={approval}
          onRespondApproval={handleApprove}
          onNewSession={handleNewSession}
          onDeleteSession={handleDeleteSession}
          onCreateAssistant={() => setShowCreateModal(true)}
          onDeleteAssistant={handleDeleteAssistant}
          onSelectAssistant={(id) => { setSelectedAssistantId(id); setSelectedWorkflowId(""); }}
          onSelectSession={handleSelectSession}
          onSessionUpdated={() => loadSessions(selectedAssistantId)}
          model={model}
          onModelChange={handleModelChange}
          hermes={hermes}
          backendStatus={backendStatus}
          skills={skills}
          showSkills={showSkills}
          onToggleSkills={() => setShowSkills((s) => !s)}
          showMarket={showMarket}
          setShowMarket={setShowMarket}
          runError={runError}
          onClearRunError={() => setRunError(null)}
          manifests={manifests}
          selectedWorkflowId={selectedWorkflowId}
          onSelectWorkflow={(id) => { setSelectedWorkflowId(id); setResultPanelCollapsed(false); }}
          onApiKeySaved={(key) => { setApiKey(key); setApiKeySet(true); }}
          aguiPort={aguiPort}
          resultPanelOpen={resultPanelOpen}
          onToggleResultPanel={() => setResultPanelOpen((o) => !o)}
          onOpenPreviewUrl={(url) => {
            setExternalPreviewUrl(url);
            setResultPanelOpen(true);
          }}
          externalPreviewUrl={externalPreviewUrl}
          setExternalPreviewUrl={setExternalPreviewUrl}
          resultPanelCollapsed={resultPanelCollapsed}
          onToggleResultPanelCollapse={() => setResultPanelCollapsed((c) => !c)}
          resultPanelWidth={resultPanelWidth}
          setResultPanelWidth={setResultPanelWidth}
        />
      </div>
      {showCreateModal && (
        <CreateAssistantModal
          skills={skills}
          onCreate={handleCreateAssistant}
          onClose={() => setShowCreateModal(false)}
        />
      )}
      {showSettings && (
        <SettingsPanel
          apiKey={apiKey}
          hasApiKey={apiKeySet || !!apiKey}
          model={model}
          theme={theme}
          onThemeChange={handleThemeChange}
          onEditApiKey={() => {
            setShowSettings(false);
            setShowKeyModal(true);
          }}
          onClose={() => setShowSettings(false)}
        />
      )}
      <ConfirmModal
        open={!!confirmDialog}
        title={confirmDialog?.title || ""}
        message={confirmDialog?.message || ""}
        danger={confirmDialog?.danger || false}
        onConfirm={confirmDialog?.onConfirm}
        onClose={() => setConfirmDialog(null)}
      />
    </ErrorBoundary>
  );
}
