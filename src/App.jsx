import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import ChatLayout from "./components/ChatLayout.jsx";
import { rememberWorkspace } from "./components/Composer.jsx";
import ApiKeyModal from "./components/ApiKeyModal.jsx";
import MarketPanel from "./components/MarketPanel.jsx";
import CreateAssistantModal from "./components/CreateAssistantModal.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";
import Launcher from "./components/Launcher.jsx";
import TabBar from "./components/TabBar.jsx";
import TopBar from "./components/TopBar.jsx";
import IconRail from "./components/IconRail.jsx";
import ResultPanel from "./components/ResultPanel.jsx";
import StudioWorkbench from "./workbenches/StudioWorkbench.jsx";
import BrowserPanel from "./components/BrowserPanel.jsx";
import ConfirmModal from "./components/ConfirmModal.jsx";
import BlockRequestDialog from "./components/BlockRequestDialog.jsx";
import WechatBindModal from "./components/WechatBindModal.jsx";
import { initContract, listManifests } from "./contract/registry.js";
import { launcherApps } from "./contract/manifests.generated.js";
import { subscribeContractEvents } from "./contract/eventBus.js";
import { useTts } from "./hooks/useTts.jsx";
import { stripMarkdownToText } from "./utils/stripMarkdown.js";
import excalidrawIcon from "./assets/excalidraw.png";
import appChatIcon from "./assets/app-chat.png";
import appManjuIcon from "./assets/app-manju.png";
import appPaperIcon from "./assets/app-paper.png";
import aihotIcon from "./assets/aihot.png";
import anatomyIcon from "./assets/anatomy.png";

// Map of manifest id / special key → vite-imported PNG so the same launcher
// art shows in BOTH the homepage grid AND the browser-style tab strip.
// `launcherApps[].iconSrc` is a plain string ("app-manju.png") which vite
// does NOT resolve — this table is the only reliable source of a hash-based
// asset URL. Keep in sync with homepageApps and App's openAppAsNewTab/openApp
// call sites so every tab carries the right icon.
const LAUNCHER_ICONS = {
  chat: appChatIcon,
  manjucraft_agent: appManjuIcon,
  paper_rewriter_agent: appPaperIcon,
  anatomyatelier: anatomyIcon,
};
import { sanitizeMessageContent } from "./components/MessageThread.jsx";
import { useAgentStream } from "./hooks/useAgentStream.js";
import { usePaperRewriteArtifacts } from "./hooks/usePaperRewriteArtifacts.js";
import { useTaskManager } from "./components/TaskPanel.jsx";
import { ErrorBoundary } from "./ErrorBoundary.jsx";

// Module-level wrapper so the studio view can be created by a function call
// instead of inline JSX. esbuild's JSX extraction for the conditional const
// (`studioView = cond ? <JSX/> : null`) was dropping local variable bindings
// (activeManifest, studioRunHandler, handleWorkflowRun) in the extracted helper,
// causing "X is not defined" at runtime. A plain function call passes values
// explicitly, avoiding the helper scope-split bug.
function StudioHost({ manifest, session, model, backendStatus, onExit, onRun }) {
  return (
    <div className="workbench-host">
      <StudioWorkbench
        manifest={manifest}
        session={session}
        onExit={onExit}
        model={model}
        backendStatus={backendStatus}
        onRun={onRun}
      />
    </div>
  );
}

// Module-level runner used for workflow onRun/onWorkflowRun props. The actual
// implementation lives on `handleWorkflowRunRef.current` inside the App
// component; we mirror that ref onto this module-level holder so the JSX
// props never have to close over a component-local variable. This avoids the
// esbuild minify bug where local variable bindings were dropped inside
// extracted conditional-JSX helpers.
const workflowRunRef = { current: null };
export function runStudioWorkflow(manifest, inputObj) {
  return workflowRunRef.current?.(manifest, inputObj);
}

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

/** 判断会话是否由微信 bridge（后端进程）独占写入。renderer 对这些会话只读不写，
 *  否则会把内存里的过期快照 flush 回 storage，冲掉 bridge 后续追加的消息。 */
function isWechatManaged(s) {
  if (!s) return false;
  if (s.source === 'wechat') return true;
  // 兼容存量会话：早期 bridge 创建时未写 source 字段，靠标题前缀兜底。
  return typeof s.title === 'string' && s.title.startsWith('微信');
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
  browserPanelOpen = false,
  onToggleBrowserPanel = () => {},
  onOpenBrowserPanel = () => {},
  onDetachResultPanel,
  studioEntry = false,
  paperRuns,
  onOpenWechatBind = () => {},
  wechatStatus = { state: "idle", bound: false },
  onOpenLiveStudio = () => {},
}) {
  // Keep a live ref to the session list so the settle callback (which is
  // identity-stable by design) can look up titles without going stale.
  const sessionsListRef = useRef(sessions);
  sessionsListRef.current = sessions;
  const hermesRef = useRef(hermes);
  hermesRef.current = hermes;
  const onSessionUpdatedRef = useRef(onSessionUpdated);
  onSessionUpdatedRef.current = onSessionUpdated;

  // Generate a short summarized session title via the backend summarizer.
  // Fire-and-forget: caller decides what to do with the result.
  const generateSessionTitle = useCallback(async (sid, userText, assistantText) => {
    if (!aguiPort) return "";
    try {
      const res = await fetch(`http://localhost:${aguiPort}/api/session-title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userText, assistantText }),
      });
      const data = await res.json().catch(() => ({}));
      return (data.title || "").trim();
    } catch (err) {
      return "";
    }
  }, [aguiPort]);

  // Persist a session when ANY of its runs finishes — including sessions that
  // are running in the background while the user looks at a different one.
  // Before per-session streams existed this lived in an isStreaming effect,
  // which only ever fired for the foreground session (background output was
  // silently dropped).
  const handleSessionSettled = useCallback((sid, msgs) => {
    const h = hermesRef.current;
    if (!sid || !h || !Array.isArray(msgs) || msgs.length === 0) return;
    const stored = (sessionsListRef.current || []).find((s) => s.id === sid);
    // WeChat sessions are written exclusively by the bridge (backend process).
    // The renderer's in-memory snapshot is stale and would clobber the newer
    // messages the bridge keeps appending — never flush back over it.
    if (isWechatManaged(stored)) return;
    const uiMessages = msgs.map(toStorageMessage);
    const userMsg = uiMessages.find((m) => m.role === "user");
    const assistantMsg = [...uiMessages].reverse().find((m) => m.role === "assistant");
    const patch = { messages: uiMessages };
    if (assistantMsg) {
      const clean = sanitizeMessageContent(assistantMsg.content || "");
      patch.preview = clean.slice(0, 45).replace(/\n/g, " ") || "(新对话)";
    }
    // Don't hardcode the first N chars as the title. When the session still
    // has the default title, kick off an async model summary that patches
    // session.title once it returns; until then the header/sidebar fall back
    // to the assistant name. Failures keep the default title.
    if (userMsg && stored?.title === "新会话") {
      const userText = (userMsg.content || "").replace(/\n/g, " ").trim();
      const assistantText = sanitizeMessageContent(assistantMsg?.content || "").replace(/\n/g, " ").trim();
      generateSessionTitle(sid, userText, assistantText)
        .then((t) => { if (t) h.updateSession(sid, { title: t }); })
        .catch(() => {});
    }
    h.updateSession(sid, patch)
      .then(() => {
        if (onSessionUpdatedRef.current) onSessionUpdatedRef.current();
      })
      .catch((err) => console.error("session save failed", err));
  }, []);

  const {
    messages: visibleMessages,
    phase,
    thinkingText,
    error: streamError,
    isStreaming,
    uiBlocks,
    stalled,
    runningSessionIds,
    sendMessage,
    stop,
    setHistory,
    hydrateSession,
    getSessionMessages,
    dropSession,
    // ── P1 新增 ──
    reasoningText,
    statusLine,
    statusKind,
    toolStatus,
    subagents,
    moaRefs,
    moaAggregating,
    usage,
    reviewSummary,
    browserProgress,
  } = useAgentStream(aguiPort, selectedSessionId, { onSettled: handleSessionSettled });

  // Auto-open the embedded browser panel when agent uses browser_* / pw_browser_*
  // tools. 必须在 ChatShell 函数体内：visibleMessages 来自本函数的
  // useAgentStream 解构（line 274），openBrowserPanel 由 App 通过 props 传入
  // （App.jsx line 1966 <ChatShell onOpenBrowserPanel={openBrowserPanel}>）。
  // 历史坑：本 useEffect 曾被放在 App 函数体内 → visibleMessages 不可见
  // （ReferenceError）；也曾被误删（误判 ChatShell 孤儿）。ChatShell 在
  // App.jsx line 1966 被渲染，不是孤儿。
  const browserNotifiedRef = useRef(new Set());
  const seenBrowserToolIdsRef = useRef(new Set());
  useEffect(() => {
    const msgs = visibleMessages || [];
    msgs.forEach((m) => {
      if (
        typeof m.toolName === "string" &&
        (m.toolName.startsWith("browser_") || m.toolName.startsWith("pw_browser_")) &&
        m.id
      ) {
        seenBrowserToolIdsRef.current.add(m.id);
      }
    });
    if (msgs.length === 0) return;
    const freshBrowserTools = msgs.filter(
      (m) =>
        typeof m.toolName === "string" &&
        (m.toolName.startsWith("browser_") || m.toolName.startsWith("pw_browser_")) &&
        m.id &&
        !seenBrowserToolIdsRef.current.has(m.id)
    );
    if (freshBrowserTools.length === 0) return;
    freshBrowserTools.forEach((m) => seenBrowserToolIdsRef.current.add(m.id));
    const sid = selectedSessionId || "";
    if (browserNotifiedRef.current.has(sid)) return;
    browserNotifiedRef.current.add(sid);
    onOpenBrowserPanel();
  }, [visibleMessages, onOpenBrowserPanel, isStreaming, selectedSessionId]);

  // Permission mode: default (backend "ask") or yolo (session approval bypass).

  // Permission mode: default (backend "ask") or yolo (session approval bypass).
  // Pushed to the backend via the gateway; fails silently if not connected.
  const [permissionMode, setPermissionMode] = useState("default");
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null); // { message } | null
  // Queue of user messages typed while the agent is busy. Flushed FIFO when
  // the current run finishes (see effect below).
  const [queuedMessages, setQueuedMessages] = useState([]);
  // Context-usage modal: lifted here so the IconRail (chat-header replacement)
  // can open it while ChatLayout still owns the modal render.
  const [showContextUsage, setShowContextUsage] = useState(false);

  // ── TTS (edge-tts) controls — lifted here so the IconRail (left vertical
  //    bar) can host the mute / play buttons. ChatLayout still owns the
  //    auto-read effect and calls useTts() independently; the two share
  //    context state. We also compute `lastAssistant` here (instead of in
  //    ChatLayout) so the rail's play button can decide enabled/disabled
  //    without an extra callback. ──
  const {
    speak: ttsSpeak,
    stop: ttsStop,
    isPlaying: ttsIsPlaying,
    mute: ttsMute,
    setMuted: setTtsMuted,
  } = useTts();
  const lastAssistant = useMemo(() => {
    if (!visibleMessages) return null;
    for (let i = visibleMessages.length - 1; i >= 0; i--) {
      if (visibleMessages[i].role === "assistant") return visibleMessages[i];
    }
    return null;
  }, [visibleMessages]);
  const ttsCanPlay = !!lastAssistant;
  function handleTtsPlayToggle() {
    if (ttsIsPlaying) {
      ttsStop();
      return;
    }
    if (!lastAssistant) return;
    const text = stripMarkdownToText(lastAssistant.content || "");
    if (text) ttsSpeak(text, lastAssistant.id);
  }
  async function handlePermissionChange(mode) {
    setPermissionMode(mode);
    if (!selectedSessionId || !hermes || !hermes.setPermissionMode) return;
    try {
      await hermes.setPermissionMode(mode, selectedSessionId);
    } catch (err) {
      console.error("set permission mode failed", err);
    }
  }

  // ── Per-session workspace binding (docs/SESSION_WORKSPACE_SPEC.md) ──
  // Source of truth is the session record's `workspaceDir` field, but the
  // pill reflects an optimistic local override immediately: waiting for
  // updateSession + loadSessions round-trip left the pill stale for seconds
  // (and empty-session cleanup could even swallow the session in between).
  const [wsOverride, setWsOverride] = useState({});
  const workspaceDir = wsOverride[selectedSessionId] !== undefined
    ? wsOverride[selectedSessionId]
    : (session?.workspaceDir || null);
  async function handleWorkspaceChange(dir) {
    if (!selectedSessionId || !hermesRef.current?.updateSession) return;
    setWsOverride((m) => ({ ...m, [selectedSessionId]: dir || null }));
    if (dir) rememberWorkspace(dir);
    try {
      await hermesRef.current.updateSession(selectedSessionId, { workspaceDir: dir || null });
      onSessionUpdatedRef.current && onSessionUpdatedRef.current();
    } catch (err) {
      console.error("update workspace failed", err);
    }
  }
  async function handlePickWorkspace() {
    if (!hermes || !hermes.selectDirectory) return;
    try {
      const dir = await hermes.selectDirectory();
      if (dir) await handleWorkspaceChange(dir);
    } catch (err) {
      console.error("select directory failed", err);
    }
  }

  const pendingSendRef = useRef(null);

  // Queued messages belong to a session; drop them when switching away.
  useEffect(() => {
    setQueuedMessages([]);
  }, [selectedSessionId]);

  // Load persisted messages when the session or sessions list changes.
  //
  // Per-session streams (2026-08-01): switching sessions no longer stops or
  // resets anything. Each session owns its own AbortController and message
  // buffer inside useAgentStream, so a background run keeps streaming while
  // the user reads another thread. hydrateSession() only fills in the stored
  // history when the snapshot differs from what's in memory — coming back to
  // a still-running session must NOT overwrite its accumulated deltas with
  // the stale on-disk snapshot, AND the effect must not be silently gated
  // when the very first call lands before sessions has been populated.
  //
  // Deps use the `sessions` array (not the per-session derived `session`
  // memo) so that the effect still fires when a tab switch lands on a
  // session that wasn't yet in memory — otherwise the bucket would stay
  // empty until the next unrelated setSessions wipes in. A sessionsRef
  // mirror keeps reads stable without forcing a re-fire on every render.
  // effect already has the same `sessions` dependency, so a sessionsRef mirror
  // higher up in the component keeps reads stable without forcing a re-fire.
  useEffect(() => {
    if (!selectedSessionId) return;
    const sid = selectedSessionId;
    const live = (sessionsListRef.current || []).find((s) => s.id === sid);
    hydrateSession(sid, live?.messages || []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId, sessions, hydrateSession]);

  async function doSend(text, mentions, explicitThreadId) {
    if (!text.trim()) return;
    const threadId = explicitThreadId || selectedSessionId;
    // Workspace binding: resolve from the live session list so background
    // sends (explicitThreadId ≠ selected) still carry the right folder.
    const wsSource = (sessionsListRef.current || []).find((s) => s.id === threadId);
    await sendMessage(text, {
      threadId,
      assistantId: selectedAssistantId,
      skillId: assistant?.skillId,
      model,
      mentions,
      workspaceDir: wsSource?.workspaceDir || null,
    });
  }

  // Flush the current session's messages when switching away or unmounting.
  // Runs still in flight are also persisted on completion via onSettled, so
  // this only guards against losing partial output on an abrupt switch.
  useEffect(() => {
    const sid = selectedSessionId;
    return () => {
      if (!sid || !hermes) return;
      // WeChat sessions are bridge-managed: never flush the renderer's stale
      // snapshot back over the bridge's on-disk messages.
      const stored = (sessionsListRef.current || []).find((s) => s.id === sid);
      if (isWechatManaged(stored)) return;
      const msgs = getSessionMessages(sid);
      if (!msgs || msgs.length === 0) return;
      const ui = msgs.map(toStorageMessage);
      hermes.updateSession(sid, { messages: ui }).catch((err) => {
        console.error("session flush on switch failed", err);
      });
    };
  }, [selectedSessionId, hermes, getSessionMessages]);

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
        // Select the new session. hydrateSession() sees the bucket is already
        // live (doSend below writes into it) and leaves it alone.
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

  // ── Workflow run: independent session (does NOT reuse chat session) ──
  // Each workflow invocation gets its own Hermes session so chat and workflows
  // can run concurrently without aborting each other. The per-session multi-
  // stream architecture (useAgentStream Map<sessionId>) handles this natively.
  // Failures are surfaced via alert + console.error so the user can see why
  // the workflow didn't start (previously they were silently swallowed).
  const handleWorkflowRunRef = useRef(null);
  handleWorkflowRunRef.current = async (manifest, inputObj = {}) => {
    const m =
      manifest || manifests.find((x) => x.id === selectedWorkflowId) || null;
    if (!hermes) {
      const msg = "hermes 桥未加载（window.hermes 不存在），请检查 Electron 主进程。";
      console.error("[workflow run]", msg);
      alert("启动工作流失败：" + msg);
      return null;
    }
    if (!m) {
      const msg = `找不到 manifest（selectedWorkflowId=${selectedWorkflowId}），工作台可能未正确初始化。`;
      console.error("[workflow run]", msg, "available ids=", manifests.map((x) => x.id));
      alert("启动工作流失败：" + msg);
      return null;
    }
    try {
      const assistantId = selectedAssistantId || "default";
      const wfSession = await hermes.createSession(
        assistantId,
        `工作流: ${m.name || m.id}`
      );
      const envelope = {
        agent_name: m.id,
        input: inputObj,
        thread_id: wfSession.id,
      };
      const text = `请调用 langgraph_agent 工具完成任务：\n${JSON.stringify(envelope)}`;
      // Send to the dedicated workflow session — not selectedSessionId.
      await doSend(text, undefined, wfSession.id);
      // Auto-switch disabled (regression 2026-08-15: switching selectedSessionId to
      // wfSession.id unmounted StudioWorkbench and wiped all in-progress state
      // (timeline / tasks / assets / runState / topology). The workflow still
      // runs in a dedicated Hermes session in the background; the user can
      // navigate to its chat tab manually from the sidebar to watch the
      // langgraph_agent tool card + SubagentPanel loop animation.
      return wfSession.id;
    } catch (err) {
      console.error("[workflow run] failed", err);
      alert(`启动工作流失败：${err?.message || String(err)}`);
      return null;
    }
  };

  // Mirror the per-instance ref onto the module-level holder so JSX props can
  // reference the module-level `runStudioWorkflow` instead of a component-local
  // variable. This avoids the esbuild minify bug where local callbacks placed
  // inside conditional JSX lose their minified binding at runtime.
  workflowRunRef.current = handleWorkflowRunRef.current;

  // ── Detach: open the standalone window AND clear the in-window state so
  //    the panel doesn't render in two places at once. We clear the active
  //    workflow (which removes its tab + exits workflow mode in the right
  //    panel) but keep `resultPanelOpen` true so the default tabs (概览 /
  //    产物 / 文件 / 变更) stay visible. The new window carries the workflow
  // NOTE: handleDetachResultPanel was relocated to the App function below.
  // Defining it here ReferenceErrors because ChatShell is a module-level
  // function with no access to App-local setters (setSelectedWorkflowId,
  // setResultPanelCollapsed). The detached click is forwarded via the
  // onDetachResultPanel prop.

  // ── Task manager: long-chain workflow tasks run independently in sidebar ──
  const taskManager = useTaskManager(
    (text) => doSend(text),
    () => { handleStop(); }
  );

  // Studio entry (launcher openMode:"newTab"): auto-focus the first RUNNING
  // task that belongs to the launched workflow so the user lands on the live
  // run instead of an empty form. Fires once tasks have loaded; re-fires if a
  // new running task appears later. Only steers when nothing is manually
  // selected yet.
  useEffect(() => {
    if (!studioEntry) return;
    if (!selectedWorkflowId) return;
    const running = taskManager.tasks.find(
      (t) => t.workflowId === selectedWorkflowId && t.status === "running"
    );
    if (running && !taskManager.selectedTaskId) {
      taskManager.onSelectTask(running.id);
    }
  }, [studioEntry, selectedWorkflowId, taskManager.tasks, taskManager.selectedTaskId, taskManager]);

  // Sidebar tab is owned by App so the chat-side AgentRunMonitor can switch to
  // the "tasks" tab on demand. (Falls back to Sidebar's internal state if null.)
  const [sidebarTab, setSidebarTab] = useState(() => {
    try { return localStorage.getItem("abcyesno:sidebarTab") || "chat"; } catch (_) { return "chat"; }
  });
  // A dismissed terminal run (so the monitor strip can be closed once done).
  const [dismissedRunId, setDismissedRunId] = useState("");

  // The live background langgraph_agent run for the foreground chat session.
  // Chat-invoked runs are auto-created in the task manager (useTaskManager
  // subscribes to workflow.graph), so this surfaces them persistently.
  const liveTask = useMemo(
    () => taskManager.tasks.find((t) => t.runId === selectedSessionId) || null,
    [taskManager.tasks, selectedSessionId]
  );
  const visibleLiveTask = liveTask
    ? !(liveTask.status !== "running" && liveTask.status !== "pending" && dismissedRunId === liveTask.runId)
      ? liveTask
      : null
    : null;

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
    // Explicitly scope the stop to the foreground session — other sessions
    // may be streaming in the background and must not be touched.
    stop(selectedSessionId);
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
      // WeChat sessions are bridge-managed: skip the full-messages write-back.
      const stored = (sessionsListRef.current || []).find((s) => s.id === selectedSessionId);
      if (isWechatManaged(stored)) return;
      const ui = newMsgs.map(toStorageMessage);
      try {
        await hermes.updateSession(selectedSessionId, { messages: ui });
        if (onSessionUpdated) onSessionUpdated();
      } catch (err) {
        console.error("delete message persist failed", err);
      }
    }
  }

  // Deleting a session must also tear down its in-memory stream state,
  // otherwise an aborted-but-live run would keep writing into a bucket that
  // no longer has a UI (and its messages would leak for the app's lifetime).
  const handleDeleteSession = useCallback(
    (id) => {
      dropSession(id);
      if (onDeleteSession) onDeleteSession(id);
    },
    [dropSession, onDeleteSession]
  );

  // Combine prop runError with stream error for the banner.
  const displayError = runError || streamError;

  return (
    <>
      <IconRail
        resultPanelOpen={resultPanelOpen}
        onToggleResultPanel={onToggleResultPanel}
        onToggleResultPanelCollapse={onToggleResultPanelCollapse}
        resultPanelCollapsed={resultPanelCollapsed}
        browserPanelOpen={browserPanelOpen}
        onToggleBrowserPanel={onToggleBrowserPanel}
        onShowContextUsage={() => setShowContextUsage(true)}
        onOpenSkills={onToggleSkills}
        onOpenWechatBind={onOpenWechatBind}
        wechatStatus={wechatStatus}
        ttsMute={ttsMute}
        onToggleTtsMute={() => setTtsMuted(!ttsMute)}
        ttsIsPlaying={ttsIsPlaying}
        ttsCanPlay={ttsCanPlay}
        onToggleTtsPlay={handleTtsPlayToggle}
        onOpenSettings={() => setShowSettings(true)}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
      />
      <Sidebar
        open={sidebarOpen}
        assistants={assistants}
        sessions={sessions}
        runningSessionIds={runningSessionIds}
        selectedAssistantId={selectedAssistantId}
        selectedSessionId={selectedSessionId}
        onSelectAssistant={onSelectAssistant}
        onSelectSession={onSelectSession}
        onCreateAssistant={onCreateAssistant}
        onDeleteAssistant={onDeleteAssistant}
        onRenameAssistant={onRenameAssistant}
        onNewSession={onNewSession}
        onDeleteSession={handleDeleteSession}
        onRenameSession={onRenameSession}
        onToggle={() => setSidebarOpen((o) => !o)}
        onOpenSkills={onToggleSkills}
        onOpenSettings={() => setShowSettings(true)}
        onOpenWechatBind={onOpenWechatBind}
        wechatStatus={wechatStatus}
        backendStatus={backendStatus}
        taskManager={taskManager}
        sidebarTab={sidebarTab}
        onTabChange={setSidebarTab}
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
        reasoningText={reasoningText}
        statusLine={statusLine}
        statusKind={statusKind}
        toolStatus={toolStatus}
        subagents={subagents}
        moaRefs={moaRefs}
        moaAggregating={moaAggregating}
        usage={usage}
        reviewSummary={reviewSummary}
        version={version}
        sidebarOpen={sidebarOpen}
        model={model}
        onToggleSidebar={() => setSidebarOpen((o) => !o)}
        onNewSession={onNewSession}
        onSend={handleSend}
        onStop={handleStop}
        onOpenKey={() => setShowKeyModal(true)}
        showContextUsage={showContextUsage}
        setShowContextUsage={setShowContextUsage}
        onModelChange={onModelChange}
        permission={permissionMode}
        onPermissionChange={handlePermissionChange}
        workspace={workspaceDir}
        onWorkspaceChange={handleWorkspaceChange}
        onPickWorkspace={handlePickWorkspace}
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
        browserPanelOpen={browserPanelOpen}
        onToggleBrowserPanel={onToggleBrowserPanel}
        onOpenBrowser={onOpenBrowserPanel}
        selectedSessionId={selectedSessionId}
        onEditMessage={handleEditMessage}
        onDeleteMessage={handleDeleteMessage}
        editingMessageId={editingMessageId}
        onSaveEdit={handleSaveEdit}
        onCancelEdit={handleCancelEdit}
        liveTask={visibleLiveTask}
        onStopLiveTask={taskManager.stopTask}
        onOpenLiveTaskDetail={(id) => { setSidebarTab("tasks"); taskManager.onSelectTask(id); }}
        onOpenLiveStudio={onOpenLiveStudio}
        onDismissLiveTask={() => liveTask && setDismissedRunId(liveTask.runId)}
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
        onWorkflowRun={runStudioWorkflow}
        model={model}
        backendStatus={backendStatus}
        onSelectWorkflow={onSelectWorkflow}
        externalPreviewUrl={externalPreviewUrl}
        onClearExternalPreview={() => setExternalPreviewUrl(null)}
        collapsed={resultPanelCollapsed}
        onToggleCollapse={onToggleResultPanelCollapse}
        style={!resultPanelCollapsed ? { width: resultPanelWidth, minWidth: resultPanelWidth, flexShrink: 0 } : undefined}
        onDetachResultPanel={onDetachResultPanel}
        paperRuns={paperRuns}
      />
        </div>
      )}
      </ResultPanelErrorBoundary>
      {/* BrowserPanel wrapped in the same ErrorBoundary so a crash never blanks the chat */}
      <ResultPanelErrorBoundary>
      {browserPanelOpen && (
        <BrowserPanel progress={browserProgress} />
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

export default function App({ aguiPort, initialWorkflowId = "", studioEntry = false }) {
  const [assistants, setAssistants] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [selectedAssistantId, setSelectedAssistantId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [model, setModel] = useState("agnes-2.5-flash");
  const [apiKey, setApiKey] = useState("");
  const [apiKeySet, setApiKeySet] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showWechatBind, setShowWechatBind] = useState(false);
  const [wechatStatus, setWechatStatus] = useState({ state: "idle", bound: false });
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [showMarket, setShowMarket] = useState(false);
  const [version, setVersion] = useState("");
  const [approval, setApproval] = useState(null);
  const lastApprovalKeyRef = useRef("");
  // P0 阻塞请求队列：sudo / secret / terminal.read 三类 _block() 请求。
  // 后端每条请求带 request_id，前端必须回执才能解除挂起。用队列支持连续多个。
  const [blockQueue, setBlockQueue] = useState([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  // studioEntry: legacy standalone-window hint (no longer used — 漫剧go now
  // opens as a normal in-app tab). Kept as a harmless no-op prop for now.
  const [resultPanelOpen, setResultPanelOpen] = useState(studioEntry ? true : false);
  const [resultPanelCollapsed, setResultPanelCollapsed] = useState(studioEntry ? false : false);
  const [resultPanelWidth, setResultPanelWidth] = useState(() => {
    try { return Number(localStorage.getItem('abc:resultPanelWidth')) || 380; } catch { return 380; }
  });
  const [browserPanelOpen, setBrowserPanelOpen] = useState(false);
  const toggleBrowserPanel = useCallback(() => setBrowserPanelOpen((o) => !o), []);
  const openBrowserPanel = useCallback(() => setBrowserPanelOpen(true), []);


  // 论文重写 dashboard 产物（单例轮询，传给 ResultPanel 的「论文产物」tab）。
  // 服务未就绪时静默降级，不干扰其它会话。
  const { runs: paperRuns } = usePaperRewriteArtifacts();
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

  // Global shortcut: F10 opens the Settings panel from anywhere in the app.

  // Global shortcut: F10 opens the Settings panel from anywhere in the app.
  useEffect(() => {
    if (!hermes || !hermes.onOpenSettings) return;
    const cb = () => setShowSettings(true);
    hermes.onOpenSettings(cb);
    return () => {
      if (hermes.offOpenSettings) hermes.offOpenSettings(cb);
    };
  }, []);

  // Renderer-side F12 fallback: when focus is inside the app DOM (main window,
  // inputs, modals), catching keydown here reliably opens DevTools even if the
  // OS/global F12 registration was stolen by another app. The main process
  // "open-devtools" IPC toggles the dock. Webview-focused F12 still goes through
  // the globalShortcut path in main.js.
  // After opening, we push keyboard focus back to the main-window composer so
  // that the NEXT F12 press is still caught here and toggles the dock closed
  // (otherwise focus lands in the DevTools panel and the key is lost).
  useEffect(() => {
    if (!hermes || !hermes.openDevTools) return;
    function pullFocusToComposer() {
      // Close the Settings modal so the composer is reachable, then move
      // keyboard focus back into the main window DOM. This lets a subsequent
      // F12 press be caught by this listener to toggle DevTools closed.
      setShowSettings(false);
      setTimeout(() => {
        try {
          const el = document.querySelector('.composer-input, textarea, input');
          if (el && el.focus) el.focus();
          else if (document.body && document.body.focus) document.body.focus();
        } catch (_) {}
      }, 120);
    }
    function onKey(e) {
      if (e.key === 'F12') {
        e.preventDefault();
        hermes.openDevTools()
          .then(pullFocusToComposer)
          .catch((err) => console.error('openDevTools failed', err));
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Contract layer: manifests drive the generic rendering components, and
  // selectedWorkflowId remembers which contract workflow (if any) is active in
  // the composer. Adding a workflow never touches this file beyond the data.
  const [manifests, setManifests] = useState(() => listManifests());
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(studioEntry ? initialWorkflowId || "" : "");

  // ── Browser-style tabs ──────────────────────────────────────────────
  // Each tab owns a snapshot of the per-surface state (assistant / session /
  // workflow / result panel). ChatShell stays mounted at all times (hidden via
  // CSS when the active tab is the homepage) so background streams never drop
  // when you switch tabs. Switching a tab saves the current surface state into
  // the leaving tab and restores the target tab's saved state into the global
  // variables ChatShell reads — effectively each tab is an independent surface.
  const [tabs, setTabs] = useState(() => {
    if (studioEntry && initialWorkflowId) {
      // Legacy standalone app window path (no longer used — 漫剧go opens as a
      // normal in-app tab). Kept for backward-compat if ever re-enabled.
      return [
        { id: "tab-studio", type: "chat", title: "漫剧go", icon: "film", assistantId: "", sessionId: "", workflowId: initialWorkflowId, resultOpen: true, resultCollapsed: false },
      ];
    }
    return [
      { id: "tab-home", type: "homepage", title: "启动台", icon: "home", assistantId: "", sessionId: "", workflowId: "", resultOpen: false, resultCollapsed: false },
    ];
  });
  const [activeTabId, setActiveTabId] = useState(studioEntry && initialWorkflowId ? "tab-studio" : "tab-home");
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;

  const persistActiveInto = useCallback((leavingId) => {
    setTabs((prev) => prev.map((t) => t.id === leavingId
      ? { ...t, assistantId: selectedAssistantId, sessionId: selectedSessionId, workflowId: selectedWorkflowId, resultOpen: resultPanelOpen, resultCollapsed: resultPanelCollapsed }
      : t));
  }, [selectedAssistantId, selectedSessionId, selectedWorkflowId, resultPanelOpen, resultPanelCollapsed]);

  const applyTabState = useCallback((tab) => {
    setSelectedAssistantId(tab.assistantId || "");
    setSelectedSessionId(tab.sessionId || "");
    setSelectedWorkflowId(tab.workflowId || "");
    setResultPanelOpen(!!tab.resultOpen);
    setResultPanelCollapsed(!!tab.resultCollapsed);
  }, []);

  const activateExisting = useCallback((id) => {
    if (id === activeTabId) return;
    persistActiveInto(activeTabId);
    const tab = tabsRef.current.find((t) => t.id === id);
    if (tab) applyTabState(tab);
    setActiveTabId(id);
  }, [activeTabId, persistActiveInto, applyTabState]);

  const createTab = useCallback((partial) => {
    // Save the current surface state into the tab we're leaving, then open a
    // new tab and bind the global surface state to it.
    persistActiveInto(activeTabId);
    const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const tab = {
      id, type: "homepage", title: "启动台", icon: "home",
      assistantId: "", sessionId: "", workflowId: "", resultOpen: false, resultCollapsed: false,
      ...partial,
    };
    setTabs((prev) => [...prev, tab]);
    applyTabState(tab);
    setActiveTabId(id);
    return id;
  }, [activeTabId, persistActiveInto, applyTabState]);

  // Open a StudioWorkbench tab bound to a running background langgraph_agent
  // task. The workbench subscribes to the task's workflow.* events and renders
  // node progress / artifacts inline; the launcher-grid open path doesn't
  // carry a taskId so it stays untouched.
  const openLiveStudio = useCallback((task) => {
    if (!task) return;
    const wf = task.workflowId || task.workflowName;
    if (!wf) return;
    const assistant = assistants.find((a) => a.id === task.assistantId) || null;
    const launcherKey = wf; // manifest key in LAUNCHER_ICONS
    const iconSrc = LAUNCHER_ICONS[launcherKey] || appManjuIcon;
    const tabKey = `${wf}::${task.id}`;
    const existing = tabsRef.current.find(
      (t) => t.type === "studio" && t.studioKey === tabKey
    );
    if (existing) {
      activateExisting(existing.id);
      return;
    }
    createTab({
      type: "studio",
      title: `${assistant?.name || "工作台"} · ${task.workflowName || wf}`,
      icon: "default",
      iconSrc,
      workflowId: wf,
      studioKey: tabKey,
      taskId: task.id,
      runId: task.runId,
      resultOpen: true,
      resultCollapsed: false,
      assistantId: task.assistantId || selectedAssistantId || "",
    });
  }, [assistants, selectedAssistantId, createTab, activateExisting]);

  // Convert the current homepage tab into an app tab instead of opening a new
  // tab. The user can press "+" if they actually want a fresh homepage.
  const openApp = useCallback((partial) => {
    const active = tabsRef.current.find((t) => t.id === activeTabId);
    if (active && active.type === "homepage") {
      const updated = { ...active, ...partial };
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? updated : t));
      applyTabState(updated);
    } else {
      createTab(partial);
    }
  }, [activeTabId, applyTabState, createTab]);

  const closeTab = useCallback((id) => {
    // Keep at least one tab open.
    if (tabs.length <= 1) return;
    const idx = tabs.findIndex((t) => t.id === id);
    const remaining = tabs.filter((t) => t.id !== id);
    setTabs(remaining);
    if (id === activeTabId) {
      const neighbor = remaining[Math.min(idx, remaining.length - 1)] || remaining[0];
      if (neighbor) {
        applyTabState(neighbor);
        setActiveTabId(neighbor.id);
      }
    }
  }, [tabs, activeTabId, applyTabState]);

  // Click on the top-bar brand area → jump to a homepage tab. Reuses an
  // existing one if there is one; otherwise creates a fresh homepage tab
  // (which is also what the "+" button does, so the behaviour stays
  // consistent). Uses tabsRef so the lookup is fresh without re-creating
  // the callback on every tabs change.
  const goHome = useCallback(() => {
    const home = tabsRef.current.find((t) => t.type === "homepage");
    if (home) {
      activateExisting(home.id);
    } else {
      createTab({ type: "homepage" });
    }
  }, [activateExisting, createTab]);

  // Tear a tab out of the main window into its own BrowserWindow.
  //   browser → webview in detached window (Phase 1, fully shipped)
  //   studio  → detached workbench window (workflowId/manifest carry over)
  //   homepage / chat → dropped for now (require App state reconstruction
  //                          that's not yet wired — see TAB_TEAROFF_SPEC §5)
  // The tab is removed from the main window's list; the detached window
  // owns its own lifecycle from then on.
  const tearOffTab = useCallback(async (tab) => {
    if (!tab) return;
    if (tabs.length <= 1) return; // keep at least one tab, like closeTab
    if (tab.type !== "browser" && tab.type !== "studio") return;
    try {
      const payload = {
        type: tab.type,
        title: tab.title,
      };
      if (tab.type === "browser") {
        payload.browserUrl = tab.browserUrl || "";
      } else if (tab.type === "studio") {
        payload.workflowId = tab.workflowId || "";
        payload.assistantId = tab.assistantId || "";
      }
      const res = await window.hermes?.tearOffTab?.(payload);
      if (res && res.success !== false) {
        closeTab(tab.id);
      }
    } catch (err) {
      console.error("tear-off-tab failed", err);
    }
  }, [tabs.length, closeTab]);

  // Reorder tabs in response to TabBar drag. `from` is the original index of
  // the dragged tab, `to` is the destination index in the post-move array
  // (TabBar normalizes to/drop-side, so we don't have to do arithmetic here).
  // If the dragged tab was the active one, `activeTabId` doesn't need to
  // change because we reorder by id, not by tracking position.
  const reorderTabs = useCallback((from, to) => {
    setTabs((prev) => {
      if (from === to || from < 0 || from >= prev.length) return prev;
      const clampedTo = Math.max(0, Math.min(to, prev.length));
      if (clampedTo === from) return prev;
      const next = prev.slice();
      const [moved] = next.splice(from, 1);
      next.splice(clampedTo, 0, moved);
      return next;
    });
  }, []);

  // Full workbench tab: clicking the workbench's "exit" button turns the
  // current studio tab back into a homepage tab.
  const exitToHome = useCallback(() => {
    const active = tabsRef.current.find((t) => t.id === activeTabId);
    if (active && active.type === "studio") {
      const homeTab = { ...active, type: "homepage", title: "启动台", icon: "home", workflowId: "", resultOpen: false, resultCollapsed: false };
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? homeTab : t));
      applyTabState(homeTab);
    } else {
      setSelectedWorkflowId("");
      setResultPanelOpen(false);
    }
  }, [activeTabId, applyTabState]);

  // Open a launcher app as a NEW tab (never replacing the Launcher tab, and
  // never spawning a separate window). If a tab for this workflow already
  // exists, just focus it instead of duplicating.
  //
  // `app.iconSrc` (manifest) is a plain string that vite won't resolve; pull
  // the hash-based asset URL from the module-level LAUNCHER_ICONS table so
  // the new tab shows the same launcher art instead of falling back to the
  // generic Lucide icon. Without this, the tab strip rendered the placeholder
  // `film` / `book-open` icons instead of the redesigned PNGs.
  const openAppAsNewTab = useCallback((app) => {
    const existing = tabsRef.current.find(
      (t) => t.workflowId === app.workflowId && t.type === "studio"
    );
    if (existing) {
      activateExisting(existing.id);
      return;
    }
    const iconSrc = LAUNCHER_ICONS[app.key] || app.iconSrc;
    createTab({
      type: "studio",
      title: app.title,
      icon: app.icon,
      iconSrc,
      workflowId: app.workflowId,
      resultOpen: true,
      resultCollapsed: false,
      assistantId: selectedAssistantId || "",
    });
  }, [activateExisting, createTab, selectedAssistantId]);

  // Homepage app grid — data-driven from the build-time injected launcherApps
  // (generated from agent manifest.json). The "对话" entry is the base chat
  // surface, not a LangGraph workflow, so it stays hardcoded here; every other
  // entry comes from a manifest with a `launcher` field. Adding an agent that
  // exposes a launcher entry requires no edit to this file.
  //
  // iconSrc resolution: pull from module-level LAUNCHER_ICONS (vite-imported
  // hash URLs). The manifest's iconSrc is a bare string ("app-manju.png")
  // that vite does not recognise; LAUNCHER_ICONS is the only reliable asset
  // URL. The iconSrc is also forwarded into every tab created from this app
  // — see openAppAsNewTab/openApp above — so the launcher grid and the
  // browser-style tab strip always show the same artwork.
  const homepageApps = useMemo(() => [
    {
      key: "chat",
      title: "对话",
      icon: "chat",
      iconSrc: LAUNCHER_ICONS.chat,
      onClick: () => openApp({
        type: "chat",
        title: "对话",
        icon: "chat",
        iconSrc: LAUNCHER_ICONS.chat,
        assistantId: selectedAssistantId || "",
      }),
    },
    ...launcherApps.map((app) => {
      const iconSrc = LAUNCHER_ICONS[app.key] || app.iconSrc;
      const onClick =
        app.openMode === "dashboard" && app.url
          ? () => createTab({
              type: "browser",
              title: app.title,
              icon: app.icon,
              iconSrc,
              browserUrl: app.url,
            })
          : app.openMode === "newTab"
          ? () => openAppAsNewTab({ ...app, iconSrc })
          : () => openApp({
              type: "studio",
              title: app.title,
              icon: app.icon,
              iconSrc,
              workflowId: app.workflowId,
              resultOpen: true,
              resultCollapsed: false,
              assistantId: selectedAssistantId || "",
            });
      return { key: app.key, title: app.title, icon: app.icon, iconSrc, onClick };
    }),
    // Excalidraw online whiteboard — opens as a NEW in-app tab with the
    // built-in browser (Electron <webview>), NOT the system browser. The
    // browser-type tab renders a fullscreen BrowserPanel pinned to the URL.
    {
      key: "excalidraw",
      title: "Excalidraw",
      icon: "default",
      iconSrc: excalidrawIcon,
      onClick: () => {
        createTab({
          type: "browser",
          title: "Excalidraw",
          iconSrc: excalidrawIcon,
          browserUrl: "https://excalidraw.com/",
        });
      },
    },
    // Anatomy Atelier — external site opened as an in-app browser tab
    // (same pattern as Excalidraw: <webview> pinned to the URL, not the
    // system browser).
    {
      key: "anatomyatelier",
      title: "Anatomy Atelier",
      icon: "default",
      iconSrc: anatomyIcon,
      onClick: () => {
        createTab({
          type: "browser",
          title: "Anatomy Atelier",
          iconSrc: anatomyIcon,
          browserUrl: "https://anatomyatelier.vercel.app/zh",
        });
      },
    },
    // AI 热点 — opens the aihot.virxact.com AI-news aggregator in the
    // built-in browser tab (Electron <webview> via BrowserPanel), mirroring
    // the Excalidraw pattern. Icon is the site's own cyan radar mark on a
    // dark navy squircle (see src/assets/aihot.png).
    {
      key: "aihot",
      title: "AI 热点",
      icon: "default",
      iconSrc: aihotIcon,
      onClick: () => {
        createTab({
          type: "browser",
          title: "AI 热点",
          iconSrc: aihotIcon,
          browserUrl: "https://aihot.virxact.com/",
        });
      },
    },
  ], [openApp, openAppAsNewTab, createTab, selectedAssistantId]);

  // ── Detach: owns the IPC + clears in-window workflow state ──
  // Lives in App (not ChatShell) because setSelectedWorkflowId /
  // setResultPanelCollapsed are App-local setters and ChatShell is a
  // module-level function with no lexical access to them. ResultPanel
  // receives this via `onDetachResultPanel` — it must never ReferenceError.
  const handleDetachResultPanel = useCallback(async () => {
    if (!window.hermes?.detachResultPanel) return;
    try {
      const result = await window.hermes.detachResultPanel({
        workflowId: selectedWorkflowId || '',
        sessionId: selectedSessionId || '',
        tab: 'overview',
        collapsed: 'false',
      });
      if (result && result.success !== false) {
        // Drop the workflow context from this window so the tab strip +
        // tabbed workbench stop showing it. The detached window owns it now.
        setSelectedWorkflowId('');
        setResultPanelCollapsed(false);
      }
    } catch (err) {
      console.error('[App] detach failed', err);
    }
  }, [selectedWorkflowId, selectedSessionId]);

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

    // ── P0 阻塞请求：sudo / secret / terminal.read / clarify ──
    // 后端 _block() 触发，必须弹窗回执才能解除线程挂起，否则 agent 死等。
    const onSudoRequest = (payload) => {
      setBlockQueue((q) => [...q, { type: "sudo.request", ...(payload || {}) }]);
    };
    const onSecretRequest = (payload) => {
      setBlockQueue((q) => [...q, { type: "secret.request", ...(payload || {}) }]);
    };
    const onTerminalReadRequest = (payload) => {
      setBlockQueue((q) => [...q, { type: "terminal.read.request", ...(payload || {}) }]);
    };
    const onClarifyRequest = (payload) => {
      setBlockQueue((q) => [...q, { type: "clarify.request", ...(payload || {}) }]);
    };
    hermes.on("sudo-request", onSudoRequest);
    hermes.on("secret-request", onSecretRequest);
    hermes.on("terminal-read-request", onTerminalReadRequest);
    hermes.on("clarify-request", onClarifyRequest);

    const onGatewayStatus = (status) => {
      setBackendStatus((prev) => ({ ...prev, gatewayConnected: !!status.connected }));
    };
    hermes.on("gateway-status", onGatewayStatus);

    // WeChat bridge status (bound / online / error) — drives the sidebar dot.
    const onWechatStatus = (payload) => {
      setWechatStatus({
        state: payload?.state || "idle",
        bound: !!payload?.bound,
        accountMasked: payload?.accountMasked || "",
      });
    };
    if (hermes.onWechatStatus) hermes.onWechatStatus(onWechatStatus);

    // Sessions-list invalidation: emitted by main after the WeChat bridge
    // mutates storage (ensureSession / appendMessage). We re-pull the
    // session list so the WeChat conversation surfaces in the sidebar
    // even if the user is currently on a different assistant.
    const onSessionsUpdated = () => {
      if (selectedAssistantId) {
        loadSessions(selectedAssistantId);
      }
    };
    if (hermes.onSessionsUpdated) hermes.onSessionsUpdated(onSessionsUpdated);

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

    // L4: a workflow.approval event carries a gate context for the inline
    // ApprovalBubble. Deduplicate by (workflowRunId, gate_id) so repeated
    // events (e.g. the LLM calling langgraph_agent multiple times) do not
    // cause the bubble/dialog to flash or reset its UI state.
    const onContractEvent = (runId, ev) => {
      const type = ev && (ev.type || ev.name) || "";
      if (type.endsWith("approval")) {
        const p = ev.payload || {};
        const key = `${p.workflowRunId || runId || ""}::${p.gate_id || ""}`;
        if (key && key !== "::" && lastApprovalKeyRef.current === key) {
          // Same gate already surfaced; ignore duplicate.
          return;
        }
        lastApprovalKeyRef.current = key || lastApprovalKeyRef.current;
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
      hermes.off("sudo-request", onSudoRequest);
      hermes.off("secret-request", onSecretRequest);
      hermes.off("terminal-read-request", onTerminalReadRequest);
      hermes.off("clarify-request", onClarifyRequest);
      hermes.off("gateway-status", onGatewayStatus);
      if (hermes.offWechatStatus) hermes.offWechatStatus(onWechatStatus);
      if (hermes.offSessionsUpdated) hermes.offSessionsUpdated(onSessionsUpdated);
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
    // Filter out empty sessions (never had any message) and clean them from
    // storage. Sessions with a workspace binding are kept — the user may bind
    // a folder BEFORE sending the first message (that's the whole point), so
    // an intentional binding must not look like an abandoned empty session.
    const emptyIds = (list || [])
      .filter((s) => (!s.messages || s.messages.length === 0) && !s.workspaceDir)
      .map((s) => s.id);
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
    lastApprovalKeyRef.current = "";
  }

  // P0 阻塞请求应答：组装 request_id + 用户输入，经既有 gatewayRequest 通道回执后端，
  // 解除 _block() 的线程挂起。取消也回传空值以主动解除（避免死等超时兜底）。
  const handleBlockRespond = useCallback(
    async (value) => {
      const current = blockQueue[0];
      if (!current) return;
      const { type, request_id } = current;
      try {
        if (type === "sudo.request") {
          await hermes.gatewayRequest("sudo.respond", { request_id, password: value || "" }, 30000);
        } else if (type === "secret.request") {
          await hermes.gatewayRequest("secret.respond", { request_id, value: value || "" }, 30000);
        } else if (type === "terminal.read.request") {
          await hermes.gatewayRequest("terminal.read.respond", { request_id, text: value || "" }, 30000);
        } else if (type === "clarify.request") {
          await hermes.gatewayRequest("clarify.respond", { request_id, answer: value || "" }, 30000);
        }
      } catch (err) {
        console.error("block request respond failed", err);
      }
      setBlockQueue((q) => q.slice(1));
    },
    [blockQueue]
  );

  const assistant = useMemo(
    () => assistants.find((a) => a.id === selectedAssistantId),
    [assistants, selectedAssistantId]
  );
  const session = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId),
    [sessions, selectedSessionId]
  );
  const activeManifest = useMemo(
    () => manifests.find((m) => m.id === selectedWorkflowId) || null,
    [manifests, selectedWorkflowId]
  );

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  const topBar = (
    <TopBar
      tabs={tabs}
      activeTabId={activeTabId}
      onActivate={activateExisting}
      onClose={closeTab}
      onAdd={() => createTab({ type: "homepage" })}
      onGoHome={goHome}
      onReorder={reorderTabs}
      onTearOff={tearOffTab}
      onMinimize={() => window.hermes?.windowControls?.minimize?.()}
      onToggleMaximize={() => window.hermes?.windowControls?.toggleMaximize?.()}
      onCloseWindow={() => window.hermes?.windowControls?.close?.()}
    />
  );

  // App-level IconRail — used by the studio / browser early returns (those
  // branches never go through ChatShell, which is where the chat-side
  // IconRail lives with TTS / context-usage buttons driven by ChatShell
  // state). All panel states (sidebar / result / browser) are owned by App
  // (lines 996–1004), so this version is fully functional in every tab
  // that renders it — not a no-op placeholder. Buttons that need ChatShell
  // state (TTS / context-usage / skills / wechat-bind) are still left out;
  // they live on the chat-side IconRail inside ChatShell.
  const iconRail = (
    <IconRail
      resultPanelOpen={resultPanelOpen}
      onToggleResultPanel={() => setResultPanelOpen((o) => !o)}
      onToggleResultPanelCollapse={() => setResultPanelCollapsed((o) => !o)}
      resultPanelCollapsed={resultPanelCollapsed}
      browserPanelOpen={browserPanelOpen}
      onToggleBrowserPanel={toggleBrowserPanel}
      onOpenSettings={() => setShowSettings(true)}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => setSidebarOpen((o) => !o)}
    />
  );

  const overlayModals = (
    <>
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
          onOpenWechatBind={() => setShowWechatBind(true)}
          onClose={() => setShowSettings(false)}
          version={version}
        />
      )}
      {showWechatBind && (
        <WechatBindModal onClose={() => setShowWechatBind(false)} />
      )}
      <ConfirmModal
        open={!!confirmDialog}
        title={confirmDialog?.title || ""}
        message={confirmDialog?.message || ""}
        danger={confirmDialog?.danger || false}
        onConfirm={confirmDialog?.onConfirm}
        onClose={() => setConfirmDialog(null)}
      />
      {blockQueue[0] && (
        <BlockRequestDialog blockRequest={blockQueue[0]} onRespond={handleBlockRespond} />
      )}
    </>
  );

  if (activeTab.type === "browser") {
    return (
      <ErrorBoundary>
        <div className="app">
          {topBar}
          {/* Browser tabs are fullscreen <webview>; the global IconRail
              (result panel / browser panel / sidebar / settings) is
              irrelevant in that context — hide it. */}
          <div className="tab-content">
            <div className="browser-tab-host">
              <BrowserPanel fullscreen initialUrl={activeTab.browserUrl || ""} />
            </div>
          </div>
        </div>
        {overlayModals}
      </ErrorBoundary>
    );
  }

  if (activeTab.type === "studio" && activeManifest) {
    return (
      <ErrorBoundary>
        <div className="app">
          {topBar}
          {/* Workbench tabs (漫剧工作台 / 论文重写) own their own nav
              (剧本/资产/分镜/成片 + 角色/场景/道具 etc.) — the global
              IconRail (result panel / browser panel / skills / settings)
              is irrelevant in that creative context. */}
          <div className="tab-content">
            <div className="workbench-host">
              <StudioWorkbench
                manifest={activeManifest}
                session={session}
                onExit={exitToHome}
                model={model}
                backendStatus={backendStatus}
                onRun={runStudioWorkflow}
                liveRunId={activeTab.runId || null}
              />
            </div>
          </div>
        </div>
        {overlayModals}
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <div className="app">
        {topBar}
        {/* ChatShell path renders its own full IconRail (with TTS / context
            buttons driven by ChatShell state). On the launcher (homepage)
            path, ChatShell is hidden via chat-host display:none — so we
            render the App-level simplified iconRail here too, otherwise
            the launcher page has no left chrome. */}
        <div className="tab-content">
          {activeTab.type === "homepage" && iconRail}
          <>
              <div className="chat-host" style={{ display: activeTab.type === "homepage" ? "none" : "flex" }}>
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
          browserPanelOpen={browserPanelOpen}
          onToggleBrowserPanel={toggleBrowserPanel}
          onOpenBrowserPanel={openBrowserPanel}
          onDetachResultPanel={handleDetachResultPanel}
          studioEntry={studioEntry}
          paperRuns={paperRuns}
          onOpenWechatBind={() => setShowWechatBind(true)}
          wechatStatus={wechatStatus}
          onOpenLiveStudio={openLiveStudio}
        />
              </div>
              {activeTab.type === "homepage" && (
                <Launcher apps={homepageApps} />
              )}
          </>
        </div>
      </div>
      {overlayModals}
    </ErrorBoundary>
  );
}
