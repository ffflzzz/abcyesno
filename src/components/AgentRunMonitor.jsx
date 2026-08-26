import React, { useState, useMemo } from "react";
import Icon from "./Icon.jsx";
import WorkflowTimeline from "./WorkflowTimeline.jsx";
import { useResolvedArtifacts, localPathOf } from "../contract/useResolvedArtifacts.js";

const STATUS_MAP = {
  pending: { label: "等待中", cls: "task-pending", icon: "loader" },
  running: { label: "运行中", cls: "task-running", icon: "refresh" },
  completed: { label: "已完成", cls: "task-completed", icon: "check-circle" },
  failed: { label: "失败", cls: "task-failed", icon: "close" },
  stopped: { label: "已停止", cls: "task-stopped", icon: "stop" },
  // Persisted-as-running tasks restored on a fresh process (stream dead with
  // the old one). Must match TaskPanel's STATUS_MAP so both views agree.
  interrupted: { label: "已中断", cls: "task-interrupted", icon: "alert" },
};

function statusInfo(status) {
  return STATUS_MAP[status] || STATUS_MAP.pending;
}

// Derive a compact live view from the task's event log so the collapsed strip
// can show "current node / 生成中" without rendering the full timeline.
function useLiveView(task) {
  return useMemo(() => {
    const events = (task && task.events) || [];
    let currentNode = null;
    let currentNodeLabel = "";
    let progress = null;
    let episode = 0;
    let totalEpisodes = 1;
    const artifacts = [];
    const seenArt = new Set();
    for (const ev of events) {
      const t = ev.type || "";
      const p = ev.payload || {};
      if (t.endsWith("graph")) {
        totalEpisodes = p.totalEpisodes || 1;
      } else if (t.endsWith("trace")) {
        if (p.status === "running") {
          currentNode = p.node;
          currentNodeLabel = p.node;
        }
        if (typeof p.episode === "number") episode = p.episode;
      } else if (t.endsWith("progress")) {
        progress = { completed: p.completed, total: p.total, message: p.message };
        if (p.status === "running") {
          currentNode = p.step_id;
          currentNodeLabel = p.stage || p.step_id;
        }
      } else if (t.endsWith("artifact") && p.id && !seenArt.has(p.id)) {
        seenArt.add(p.id);
        artifacts.push(p);
      }
    }
    return { currentNode, currentNodeLabel, progress, episode, totalEpisodes, artifacts };
  }, [task]);
}

/**
 * AgentRunMonitor — a persistent, always-visible status bar for the foreground
 * session's background langgraph_agent run. Lives above the composer in the
 * chat so the user can observe "which node is running / 生成中 / progress /
 * artifacts" without switching to the tasks tab. Collapsible; expands to the
 * full node timeline + artifact grid inline.
 */
export default function AgentRunMonitor({ task, onStop, onOpenTaskDetail, onOpenStudio, onDismiss }) {
  const [expanded, setExpanded] = useState(false);
  const view = useLiveView(task);
  const si = statusInfo(task.status);
  const isActive = task.status === "running" || task.status === "pending";
  const resolved = useResolvedArtifacts(view.artifacts);

  return (
    <div className={`agent-run-monitor ${si.cls} ${expanded ? "expanded" : ""}`}>
      <div className="arm-header" onClick={() => setExpanded((v) => !v)}>
        <span className={`arm-status-icon ${si.cls}`}>
          <Icon name={si.icon} size={14} />
        </span>
        <div className="arm-title">
          <span className="arm-name">{task.workflowName || task.workflowId || "后台任务"}</span>
          <span className={`arm-status ${si.cls}`}>{si.label}</span>
        </div>

        <div className="arm-current">
          {isActive ? (
            <div className="arm-current-stack">
              <div className="arm-current-row">
                <span className="arm-spinner" />
                <span className="arm-current-label">
                  {view.currentNodeLabel ? `生成中：${view.currentNodeLabel}` : "生成中…"}
                  {view.totalEpisodes > 1
                    ? ` · 第 ${(view.episode || 1)}/${view.totalEpisodes} 集`
                    : ""}
                </span>
              </div>
              {view.progress && typeof view.progress.total === "number" && view.progress.total > 0 && (
                <div className="arm-progress">
                  <span className="arm-progress-bar">
                    <span
                      className="arm-progress-fill"
                      style={{
                        width: `${Math.min(100, (view.progress.completed / view.progress.total) * 100)}%`,
                      }}
                    />
                  </span>
                  <span className="arm-progress-count">
                    {view.progress.completed}/{view.progress.total}
                  </span>
                  {view.progress.message && (
                    <span className="arm-progress-message" title={view.progress.message}>
                      {view.progress.message}
                    </span>
                  )}
                </div>
              )}
            </div>
          ) : (
            <span className="arm-done-info">
              {task.artifacts && task.artifacts.length > 0
                ? `${task.artifacts.length} 个产物`
                : si.label}
            </span>
          )}
        </div>

        <div className="arm-actions" onClick={(e) => e.stopPropagation()}>
          {onOpenStudio && task.workflowId && (
            <button
              className="arm-btn"
              title="在 StudioWorkbench 里查看节点/产物"
              onClick={() => onOpenStudio(task)}
            >
              <Icon name="monitor" size={13} /> 工作台
            </button>
          )}
          {isActive && onStop && (
            <button className="arm-btn" title="停止任务" onClick={() => onStop(task.id)}>
              <Icon name="stop" size={13} /> 停止
            </button>
          )}
          {onOpenTaskDetail && (
            <button
              className="arm-btn"
              title="在任务面板查看"
              onClick={() => onOpenTaskDetail(task.id)}
            >
              <Icon name="panel" size={13} /> 详情
            </button>
          )}
          {!isActive && onDismiss && (
            <button className="arm-btn" title="收起" onClick={() => onDismiss()}>
              <Icon name="close" size={13} />
            </button>
          )}
          <button
            className="arm-btn"
            title={expanded ? "折叠" : "展开"}
            onClick={() => setExpanded((v) => !v)}
          >
            <Icon name={expanded ? "chevron-up" : "chevron-down"} size={13} />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="arm-body">
          <WorkflowTimeline events={task.events || []} />
          {view.artifacts.length > 0 && (
            <div className="arm-artifacts">
              <div className="arm-artifacts-label">产物 ({view.artifacts.length})</div>
              <div className="arm-artifacts-grid">
                {view.artifacts.map((a, i) => {
                  const key = a.id || a.label || `a${i}`;
                  const remote =
                    a.url || (a.src && /^(https?:|data:)/i.test(a.src) ? a.src : null);
                  const src = remote || resolved[key];
                  if (src) {
                    return (
                      <img
                        key={key}
                        className="arm-artifact-thumb"
                        src={src}
                        alt={a.label || `产物${i + 1}`}
                      />
                    );
                  }
                  const lp = localPathOf(a);
                  if (lp) {
                    return (
                      <div key={key} className="arm-artifact-thumb loading">
                        读取中…
                      </div>
                    );
                  }
                  return (
                    <span key={key} className="arm-artifact-chip">
                      {a.label || a.type}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
