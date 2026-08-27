import React, { useRef, useEffect, useState, useMemo } from "react";
import Icon from "./Icon.jsx";
import Composer from "./Composer.jsx";
import MessageThread from "./MessageThread.jsx";
import SkillPanel from "./SkillPanel.jsx";
import ContextUsage from "./ContextUsage.jsx";
import Toasts from "./Toasts.jsx";
import AgentRunMonitor from "./AgentRunMonitor.jsx";
import { inferStreamingPhase } from "../utils/streamingPhase.js";
import { useTts } from "../hooks/useTts.jsx";
import { stripMarkdownToText } from "../utils/stripMarkdown.js";
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
  backendSilentMs = 0,
  turnElapsedMs = 0,
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
  workspace = null,
  onWorkspaceChange,
  onPickWorkspace,
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
  onOpenLiveStudio = () => {},
  onDismissLiveTask = () => {},
  // Context-usage modal state is owned by ChatShell (so the IconRail can open
  // it); ChatLayout only renders the modal from these props.
  showContextUsage = false,
  setShowContextUsage = () => {},
}) {
  const bottomRef = useRef(null);
  const [attachment, setAttachment] = useState(null);

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

  // ── Global TTS (edge-tts) controls ──
  // Auto-read effect only: the mute / play / stop buttons live on the left
  // IconRail (state lifted to App.jsx). The two callers share context state
  // so the rail stays in sync.
  const { speak, mute, ttsSettings } = useTts();
  const lastAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return messages[i];
    }
    return null;
  }, [messages]);

  // Auto-read: fire once when the run transitions from loading → done, for the
  // newest assistant message only. Track read ids so history / re-renders never
  // re-read; reset on session switch.
  const readMsgIdsRef = useRef(new Set());
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (selectedSessionId) readMsgIdsRef.current = new Set();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId]);
  useEffect(() => {
    const justFinished = prevLoadingRef.current && !isLoading;
    prevLoadingRef.current = isLoading;
    if (!justFinished) return;
    if (!ttsSettings.autoRead || mute) return;
    if (!lastAssistant || readMsgIdsRef.current.has(lastAssistant.id)) return;
    readMsgIdsRef.current.add(lastAssistant.id);
    const text = stripMarkdownToText(lastAssistant.content || "");
    if (text) speak(text, lastAssistant.id);
  }, [isLoading, lastAssistant, ttsSettings.autoRead, mute, speak]);

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
      <div className="chat-header">
        <div className="header-left">
          <div className="header-assistant">
            <div className="header-title-row">
              <span className="header-title">{assistant?.name || "对话"}</span>
            </div>
          </div>
        </div>
      </div>

      {runError && null /* error-banner 移除：错误改为 Composer 内小 bach 头像上的气泡（更克制，不占满顶部） */}

      {approvalPending && null /* banner 移除：审批提示已由消息流内的 ApprovalBubble 承载，避免顶部 toast + 中部 banner + 卡片三层重复通知 */}

      <div className="chat-body">
        {(!messages || messages.length === 0) ? (
          <div className="welcome">
            <img src={bachAvatar} alt="Chaos" className="welcome-avatar" onClick={() => onOpenPreviewUrl && onOpenPreviewUrl("https://abcyesno.cn")} style={{ cursor: "pointer" }} title="打开 Abcyesno 文档站" />
            <h2>{assistant?.name || "Chaos"}</h2>
            {/* Description + quick-action chips intentionally hidden: the welcome
                screen used to show a 1-line blurb and three canned prompts
                ("列出当前目录文件" / "帮我做一条剪映视频" / "搜索最新 AI 新闻"),
                which read as low-effort placeholder copy and cluttered the
                empty state. The avatar + name is enough; type whatever you
                want into the composer below. Re-enable by restoring the
                <p> + <div className="welcome-hints"> block that used to live
                here. */}
          </div>
        ) : (
          <MessageThread
            messages={messages}
            loading={isLoading}
            streamPhase={phase}
            thinkingText={thinkingText}
            reasoningText={reasoningText}
            backendSilentMs={backendSilentMs}
            turnElapsedMs={turnElapsedMs}
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
          onOpenStudio={onOpenLiveStudio}
          onDismiss={onDismissLiveTask}
        />
      )}

      <Composer
          model={model}
          onModelChange={onModelChange}
          permission={permission}
          onPermissionChange={onPermissionChange}
          workspace={workspace}
          onWorkspaceChange={onWorkspaceChange}
          onPickWorkspace={onPickWorkspace}
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
          runError={runError}
          onClearRunError={onClearRunError}
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
