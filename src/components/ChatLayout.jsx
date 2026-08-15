import React, { useRef, useEffect, useState, useMemo } from "react";
import Icon from "./Icon.jsx";
import Composer from "./Composer.jsx";
import MessageThread from "./MessageThread.jsx";
import SkillPanel from "./SkillPanel.jsx";
import ContextUsage from "./ContextUsage.jsx";
import Toasts from "./Toasts.jsx";
import AgentRunMonitor from "./AgentRunMonitor.jsx";
import { inferStreamingPhase } from "../utils/streamingPhase.js";
import bachAvatar from "../assets/bach-avatar.png";

function getStatusLabel(backendStatus, phase) {
  if (!backendStatus.hermesReady) return { text: "启动中…", dot: "starting" };
  if (!backendStatus.gatewayConnected) return { text: "连接中…", dot: "connecting" };
  if (phase === "thinking") return { text: "思考中…", dot: "thinking" };
  if (phase === "tool_executing") return { text: "执行工具…", dot: "thinking" };
  if (phase === "text_generating") return { text: "生成回复…", dot: "thinking" };
  return { text: "就绪", dot: "ready" };
}

export default function ChatLayout({
  assistant,
  session,
  messages,
  status,
  streamPhase,
  thinkingText,
  uiBlocks = [],
  stalled = false,
  // ── P1 新增 ──
  reasoningText = "",
  statusLine = "",
  statusKind = "",
  toolStatus = {},
  subagents = [],
  moaRefs = [],
  moaAggregating = null,
  usage = null,
  reviewSummary = null,
  version,
  sidebarOpen,
  model,
  onToggleSidebar,
  onNewSession,
  onSend,
  onStop,
  onOpenKey,
  onModelChange,
  permission,
  onPermissionChange,
  queuedMessages = [],
  onRemoveQueued,
  backendStatus,
  skills,
  runError,
  onClearRunError,
  approval,
  onRespondApproval,
  showSkills,
  onToggleSkills,
  onRetry,
  onRegenerate,
  manifests,
  selectedWorkflowId,
  onSelectWorkflow,
  assistants = [],
  resultPanelOpen = false,
  onToggleResultPanel = () => {},
  onOpenPreviewUrl,
  resultPanelCollapsed = false,
  onToggleResultPanelCollapse = () => {},
  // ── Detach: App-level handler that opens the standalone window AND
  //    closes the in-window panel so the two don't render side by side. ──
  onDetachResultPanel = () => {},
  browserPanelOpen = false,
  onToggleBrowserPanel = () => {},
  onOpenBrowser = () => {},
  selectedSessionId = "",
  onEditMessage,
  onDeleteMessage,
  editingMessageId = null,
  onSaveEdit,
  onCancelEdit,
  // ── Persistent live status of the foreground session's background run ──
  liveTask = null,
  onStopLiveTask = () => {},
  onOpenLiveTaskDetail = () => {},
  onDismissLiveTask = () => {},
}) {
  const bottomRef = useRef(null);
  const [attachment, setAttachment] = useState(null);
  const [showContextUsage, setShowContextUsage] = useState(false);

  const approvalPending = !!approval;

  // streamPhase comes from useAgentStream via App; fallback to inferring if absent.
  const isLoading = status === "thinking";
  // hardDisabled = truly cannot type (approval gate open, backend down).
  // busy = agent working → composer stays editable, sends get queued.
  const hardDisabled = approvalPending || !backendStatus.gatewayConnected;
  const inferredPhase = useMemo(() => inferStreamingPhase(messages, isLoading), [messages, isLoading]);
  const phase = streamPhase || inferredPhase;

  // @ mention directory (spec §2.3): assistants + manifests, fed to the
  // Composer's @ picker and used to resolve sub-call routing.
  const mentionables = useMemo(
    () => [
      ...assistants.map((a) => ({ id: a.id, name: a.name || a.id, kind: "assistant" })),
      ...manifests.map((m) => ({ id: m.id, name: m.name || m.id, kind: "workflow" })),
    ],
    [assistants, manifests]
  );

  useEffect(() => {
    // Virtuoso handles scroll-to-bottom via followOutput; no manual scroll needed.
  }, [messages, status]);

  async function handleUpload(input) {
    if (!window.hermes || !session?.id) return;
    try {
      if (input && input.type === "image") {
        setAttachment(input);
        return;
      }
      let filePath = input && input.filePath ? input.filePath : null;
      if (!filePath) {
        filePath = await window.hermes.selectFile();
      }
      if (!filePath) return;
      const info = await window.hermes.uploadFile(session.id, filePath);
      setAttachment({ type: "file", ...info });
    } catch (err) {
      console.error("upload failed", err);
    }
  }

  function handleSend(content, images, mentions) {
    const hasImages = images && images.length > 0;
    if (!content.trim() && !hasImages && !attachment) return;
    let fullText = content;
    if (hasImages) {
      // Restore each inline-image placeholder to a markdown image so the model
      // sees the picture exactly where the user placed it among the text.
      fullText = fullText.replace(/\[\[IMG:(\d+)\]\]/g, (m, i) => {
        const url = images[Number(i)];
        return url ? `\n![图片${Number(i) + 1}](${url})\n` : "";
      });
    }
    if (attachment) {
      // Non-image file attachment (images are inline now): forward the real
      // on-disk path so Hermes can read the file content.
      const filePath = attachment.localPath || attachment.originalPath || attachment.filePath || "";
      const pathLine = filePath ? `\n文件路径: ${filePath}` : "";
      fullText = `[附件: ${attachment.fileName}]${pathLine}\n${fullText}`;
      setAttachment(null);
    }
    onSend(fullText, mentions);
  }

  return (
    <main className={`chat-layout ${sidebarOpen ? "" : "full"}`}>
      <header className="chat-header">
        <div className="header-left">
          {!sidebarOpen && (
            <button className="header-icon" onClick={onToggleSidebar} title="展开侧边栏">
              <Icon name="panel" size={16} />
            </button>
          )}
          <div className="header-assistant">
            <div className="header-title-row">
              <span className={`header-status-dot ${getStatusLabel(backendStatus, phase).dot}`} />
              <span className="header-title">
                {session?.title && session.title !== "新会话"
                  ? session.title
                  : (assistant?.name || "Abcyesno")}
              </span>
            </div>
          </div>
        </div>
        <div className="header-center" />
        <div className="header-right">
          <button
            className={`header-icon ${resultPanelOpen && !resultPanelCollapsed ? "active" : ""}`}
            onClick={() => {
              if (!resultPanelOpen) {
                onToggleResultPanel(); // closed → open
              } else {
                onToggleResultPanelCollapse(); // open ↔ collapsed
              }
            }}
            title={resultPanelCollapsed ? "展开结果区" : resultPanelOpen ? "收起结果区" : "打开结果区"}
          >
            <Icon name="panel" size={16} />
          </button>
          <button
            className={`header-icon ${browserPanelOpen ? "active" : ""}`}
            onClick={onToggleBrowserPanel}
            title={browserPanelOpen ? "关闭浏览器" : "打开浏览器"}
          >
            <Icon name="globe" size={16} />
          </button>
            <button
            className="header-icon"
            onClick={() => setShowContextUsage(true)}
            title="上下文用量"
          >
            <Icon name="activity" size={16} />
          </button>
          <button className="header-icon" onClick={onOpenKey} title="设置 API Key">
              <Icon name="settings" size={16} />
            </button>
        </div>
      </header>

      {runError && (
        <div className="error-banner">
          <span className="error-banner-text">错误：{runError}</span>
          <button className="error-banner-close" onClick={onClearRunError} title="关闭">
            <Icon name="close" size={14} />
          </button>
        </div>
      )}

      {approvalPending && (
        <div className="approval-banner">
          <Icon name="pause" size={14} /> 等待用户确认：操作需要批准后才会继续
        </div>
      )}

      <div className="chat-body">
        {(!messages || messages.length === 0) ? (
          <div className="welcome">
            <img src={bachAvatar} alt="ABC" className="welcome-avatar" onClick={() => onOpenPreviewUrl && onOpenPreviewUrl("https://abcyesno.cn")} style={{ cursor: "pointer" }} title="打开 Abcyesno 文档站" />
            <h2>{assistant?.name || "Abcyesno"}</h2>
            <p>{assistant?.description || "输入问题，AI 会帮你执行命令、读取文件、浏览网页"}</p>
            <div className="welcome-hints">
              <span className="hint" onClick={() => onSend && onSend("列出当前目录文件")}>“列出当前目录文件”</span>
              <span className="hint" onClick={() => onSend && onSend("帮我做一条剪映视频")}>“帮我做一条剪映视频”</span>
              <span className="hint" onClick={() => onSend && onSend("搜索最新 AI 新闻")}>“搜索最新 AI 新闻”</span>
            </div>
          </div>
        ) : (
          <MessageThread
            messages={messages}
            loading={isLoading}
            streamPhase={phase}
            thinkingText={thinkingText}
            reasoningText={reasoningText}
            uiBlocks={uiBlocks}
            stalled={stalled}
            subagents={subagents}
            moaRefs={moaRefs}
            moaAggregating={moaAggregating}
            toolStatus={toolStatus}
            reviewSummary={reviewSummary}
            onRetry={onRetry}
            onRegenerate={onRegenerate}
            assistant={assistant}
            manifests={manifests}
            onUpgradeToWorkbench={onSelectWorkflow}
            onOpenPreviewUrl={onOpenPreviewUrl}
            approval={approval}
            onRespondApproval={onRespondApproval}
            sessionId={selectedSessionId}
            onEditMessage={onEditMessage}
            onDeleteMessage={onDeleteMessage}
            editingMessageId={editingMessageId}
            onSaveEdit={onSaveEdit}
            onCancelEdit={onCancelEdit}
            onSend={onSend}
          />
        )}
        <div ref={bottomRef} style={{ display: "none" }} />
      </div>

      {liveTask && (
        <AgentRunMonitor
          task={liveTask}
          onStop={onStopLiveTask}
          onOpenTaskDetail={onOpenLiveTaskDetail}
          onDismiss={onDismissLiveTask}
        />
      )}

      <Composer
          model={model}
          onModelChange={onModelChange}
          permission={permission}
          onPermissionChange={onPermissionChange}
          onSend={handleSend}
          onStop={onStop}
          onNewSession={onNewSession}
          onUpload={handleUpload}
          onShowSkills={onToggleSkills}
          disabled={hardDisabled}
          busy={isLoading}
          mentionables={mentionables}
          queuedMessages={queuedMessages}
          onRemoveQueued={onRemoveQueued}
          onOpenPreviewUrl={onOpenPreviewUrl}
          onOpenBrowser={onOpenBrowser}
          placeholder={
            approvalPending
              ? "请先在审批弹窗中选择批准或拒绝…"
              : !backendStatus.hermesReady
              ? "引擎正在启动…"
              : !backendStatus.gatewayConnected
              ? "正在连接服务…"
              : isLoading
              ? "工作中…可继续输入，Enter 排队"
              : "输入问题，Enter 发送，Shift+Enter 换行"
          }
          attachment={attachment}
          onClearAttachment={() => setAttachment(null)}
        />

      {showSkills && (
        <SkillPanel
          skills={skills}
          manifests={manifests}
          selectedWorkflowId={selectedWorkflowId}
          onSelectWorkflow={onSelectWorkflow}
          onClose={onToggleSkills}
        />
      )}
      <div className="version-tag">Abcyesno {version ? `v${version}` : ""}</div>

      <ContextUsage
        messages={messages}
        model={model}
        usage={usage}
        open={showContextUsage}
        onClose={() => setShowContextUsage(false)}
      />

      <Toasts />
    </main>
  );
}
