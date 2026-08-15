import React, { useState, useEffect, useMemo, useCallback } from "react";
import Icon from "./Icon.jsx";
import { subscribeContractEvents, getContractEvents } from "../contract/eventBus.js";
import { getManifest, listManifests } from "../contract/registry.js";
import WorkflowTimeline from "./WorkflowTimeline.jsx";
import ArtifactViewer from "./ArtifactViewer.jsx";

// ── Task model ──
// Each workflow run becomes a "task" that lives independently in the sidebar.
// The main chat is never blocked — tasks run in the background.

const STATUS_MAP = {
  pending: { label: "等待中", icon: "loader", cls: "task-pending" },
  running: { label: "运行中", icon: "refresh", cls: "task-running" },
  completed: { label: "已完成", icon: "check-circle", cls: "task-completed" },
  failed: { label: "失败", icon: "close", cls: "task-failed" },
  stopped: { label: "已停止", icon: "stop", cls: "task-stopped" },
};

function formatTaskTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const diffMs = now - d;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}小时前`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function statusInfo(status) {
  return STATUS_MAP[status] || STATUS_MAP.pending;
}

// Turn an agent id like "manjucraft_agent" into a friendly display name "Manjucraft Agent".
function friendlyName(agent) {
  if (!agent) return "后台任务";
  return String(agent)
    .split(/[_\s-]+/)
    .map((s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s))
    .join(" ");
}

// ── Compact progress bar for running tasks ──
function TaskProgressBar({ events }) {
  if (!events || events.length === 0) return null;

  // Collect progress steps
  const steps = [];
  const seen = new Set();
  for (const ev of events) {
    if (ev.type === "workflow.progress") {
      const p = ev.payload || {};
      const key = p.step_id || p.step || p.label || "";
      if (key && !seen.has(key)) {
        seen.add(key);
        steps.push({ id: key, label: p.label || p.step || key, done: p.done !== false });
      }
    }
  }

  if (steps.length === 0) return null;

  const doneCount = steps.filter((s) => s.done).length;
  const pct = Math.round((doneCount / steps.length) * 100);

  return (
    <div className="task-progress-bar">
      <div className="task-progress-fill" style={{ width: `${pct}%` }} />
      <span className="task-progress-text">{doneCount}/{steps.length}</span>
    </div>
  );
}

// ── Single task card in the list ──
function TaskCard({ task, active, onClick }) {
  const si = statusInfo(task.status);
  const events = task.events || [];

  return (
    <div
      className={`task-card ${active ? "active" : ""} ${si.cls}`}
      onClick={() => onClick && onClick(task.id)}
    >
      <div className="task-card-header">
        <span className="task-status-icon"><Icon name={si.icon} size={14} /></span>
        <div className="task-card-title-row">
          <span className="task-card-name">{task.workflowName || task.workflowId || "任务"}</span>
          <span className="task-card-time">{formatTaskTime(task.startedAt)}</span>
        </div>
      </div>
      <div className="task-card-status">
        <span className={`task-status-label ${si.cls}`}>{si.label}</span>
        {task.status === "running" && <TaskProgressBar events={events} />}
      </div>
      {(task.status === "completed" || task.status === "failed") && task.artifacts && task.artifacts.length > 0 && (
        <div className="task-artifacts-hint">
          <Icon name="folder" size={14} /> {task.artifacts.length} 个产物
        </div>
      )}
    </div>
  );
}

// ── Task detail view (when a task is selected) ──
function TaskDetail({ task, onStop, onSend }) {
  const [detailTab, setDetailTab] = useState("progress"); // progress | artifacts | log
  const events = task.events || [];
  const artifacts = useMemo(() => {
    const map = new Map();
    for (const ev of events) {
      if (ev.type === "workflow.artifact" && ev.payload) {
        const p = ev.payload;
        if (p.id) map.set(p.id, p);
        else map.set(JSON.stringify(p), p);
      }
    }
    return Array.from(map.values()).reverse();
  }, [events]);

  const manifest = task.workflowId ? getManifest(task.workflowId) : null;

  return (
    <div className="task-detail">
      <div className="task-detail-header">
        <span className="task-detail-name">{task.workflowName || task.workflowId}</span>
        <span className={`task-status-badge ${statusInfo(task.status).cls}`}>
          <Icon name={statusInfo(task.status).icon} size={12} /> {statusInfo(task.status).label}
        </span>
      </div>

      <div className="task-detail-tabs">
        <button
          className={`task-dtab ${detailTab === "progress" ? "active" : ""}`}
          onClick={() => setDetailTab("progress")}
        >
          进度
        </button>
        <button
          className={`task-dtab ${detailTab === "artifacts" ? "active" : ""}`}
          onClick={() => setDetailTab("artifacts")}
        >
          产物{artifacts.length > 0 ? ` (${artifacts.length})` : ""}
        </button>
        <button
          className={`task-dtab ${detailTab === "log" ? "active" : ""}`}
          onClick={() => setDetailTab("log")}
        >
          日志
        </button>
      </div>

      <div className="task-detail-body">
        {detailTab === "progress" && (
          <div className="task-detail-progress">
            {manifest && (
              <div className="task-detail-manifest">
                <span className="task-detail-manifest-name">{manifest.name}</span>
                <span className="task-detail-manifest-desc">{manifest.description || ""}</span>
              </div>
            )}
            <WorkflowTimeline events={events} compact />
            {(task.status === "running" || task.status === "pending") && (
              <button
                className="task-stop-btn"
                onClick={() => onStop && onStop(task.id)}
              >
                <Icon name="stop" size={14} /> 停止任务
              </button>
            )}
          </div>
        )}

        {detailTab === "artifacts" && (
          <div className="task-detail-artifacts">
            {artifacts.length === 0 ? (
              <div className="task-empty">尚无产物</div>
            ) : (
              artifacts.map((a) => (
                <div key={a.id || a.label} className="task-artifact-item">
                  <ArtifactViewer artifact={a} />
                </div>
              ))
            )}
          </div>
        )}

        {detailTab === "log" && (
          <div className="task-detail-log">
            {events.length === 0 ? (
              <div className="task-empty">暂无事件日志</div>
            ) : (
              events.map((ev, i) => (
                <div key={i} className={`task-log-entry type-${(ev.type || "").replace(".", "-")}`}>
                  <span className="task-log-type">{ev.type}</span>
                  <span className="task-log-payload">
                    {ev.payload ? JSON.stringify(ev.payload).slice(0, 200) : ""}
                  </span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main TaskPanel ──
export default function TaskPanel({
  tasks,
  selectedTaskId,
  onSelectTask,
  onStopTask,
  onClearCompleted,
  onClearAll,
}) {
  // Auto-select first running task
  const runningTasks = tasks.filter((t) => t.status === "running");
  const completedTasks = tasks.filter((t) => t.status === "completed" || t.status === "failed" || t.status === "stopped");
  const pendingTasks = tasks.filter((t) => t.status === "pending");

  const activeTask = tasks.find((t) => t.id === selectedTaskId);

  return (
    <div className="task-panel">
      {/* Task list */}
      <div className="task-list-area">
        {tasks.length > 0 && (
          <div className="task-list-header">
            <span className="task-count">共 {tasks.length} 个任务</span>
            {onClearAll && (
              <button className="task-clear-all-btn" onClick={onClearAll} title="清除所有任务（含运行中）">
                <Icon name="trash" size={12} /> 清除全部
              </button>
            )}
          </div>
        )}
        {(runningTasks.length > 0 || pendingTasks.length > 0) && (
          <>
            <div className="task-group-label">进行中 ({runningTasks.length + pendingTasks.length})</div>
            {[...pendingTasks, ...runningTasks].map((t) => (
              <TaskCard
                key={t.id}
                task={t}
                active={selectedTaskId === t.id}
                onClick={onSelectTask}
              />
            ))}
          </>
        )}

        {completedTasks.length > 0 && (
          <>
            <div className="task-group-label">
              已完成 ({completedTasks.length})
              <button className="task-clear-btn" onClick={onClearCompleted}>清空</button>
            </div>
            {completedTasks.map((t) => (
              <TaskCard
                key={t.id}
                task={t}
                active={selectedTaskId === t.id}
                onClick={onSelectTask}
              />
            ))}
          </>
        )}

        {tasks.length === 0 && (
          <div className="task-empty-state">
            <div className="task-empty-icon"><Icon name="zap" size={28} /></div>
            <div className="task-empty-text">暂无后台任务</div>
            <div className="task-empty-hint">从「工作流」Tab 触发工作流后，任务会在这里独立运行</div>
          </div>
        )}
      </div>

      {/* Detail panel */}
      {activeTask && (
        <div className="task-detail-area">
          <TaskDetail
            task={activeTask}
            onStop={onStopTask}
          />
        </div>
      )}
    </div>
  );
}

// ── Hook: manage task lifecycle ──
// Call this in App.jsx to own the task state and bridge eventBus → tasks.
export function useTaskManager(onSend, onStop) {
  const [tasks, setTasks] = useState(() => {
    try {
      const raw = JSON.parse(localStorage.getItem("abcyesno:tasks") || "[]");
      // Filter out tasks whose workflowId no longer exists in the manifest registry
      const validIds = new Set((listManifests() || []).map((m) => m.id));
      return raw.filter((t) => !t.workflowId || validIds.has(t.workflowId));
    } catch (_) {
      return [];
    }
  });
  const [selectedTaskId, setSelectedTaskId] = useState("");

  // Persist tasks to localStorage
  useEffect(() => {
    try {
      localStorage.setItem("abcyesno:tasks", JSON.stringify(tasks));
    } catch (_) {}
  }, [tasks]);

  // Subscribe to contract events to update running tasks
  useEffect(() => {
    const unsub = subscribeContractEvents((runId, ev) => {
      const evType = ev && (ev.type || "");
      setTasks((prev) => {
        const existing = prev.find((t) => t.runId === runId);

        // ── Auto-create a task for chat-invoked langgraph_agent runs ──
        // The workbench path calls createTask() explicitly, but when the user
        // triggers langgraph_agent from the chat there is no task yet. We
        // create one on the first workflow.graph / workflow.started so the run
        // is observable + persisted (localStorage) without leaving the chat.
        if (!existing) {
          if (evType === "workflow.graph" || evType === "workflow.started") {
            const p = ev.payload || {};
            const agent = p.agent || "langgraph_agent";
            const newTask = {
              id: `task-${runId}-${Date.now()}`,
              runId,
              workflowId: agent,
              agentName: agent,
              workflowName: friendlyName(agent),
              status: "running",
              startedAt: Date.now(),
              completedAt: null,
              input: null,
              sessionId: runId,
              events: [ev],
              artifacts: [],
            };
            return [newTask, ...prev];
          }
          return prev; // Not a workflow run we care about
        }

        // ── Re-run in the same session: restart the existing task ──
        if (
          (evType === "workflow.graph" || evType === "workflow.started") &&
          (existing.status === "completed" || existing.status === "failed" || existing.status === "stopped")
        ) {
          return prev.map((t) =>
            t.runId === runId
              ? { ...t, status: "running", startedAt: Date.now(), completedAt: null, events: [ev], artifacts: [] }
              : t
          );
        }

        // ── Normal update of an existing (running) task ──
        let statusUpdate = null;
        if (evType === "workflow.progress" || evType === "workflow.step") {
          statusUpdate = "running";
        } else if (evType === "workflow.complete" || evType === "workflow.done") {
          statusUpdate = "completed";
        } else if (evType === "workflow.error" || evType === "workflow.fail") {
          statusUpdate = "failed";
        } else if (evType === "workflow.stopped" || evType === "workflow.interrupted") {
          statusUpdate = "stopped";
        }

        // Collect artifacts
        let newArtifacts = existing.artifacts || [];
        if (evType === "workflow.artifact" && ev.payload) {
          const p = ev.payload;
          if (p.id && !newArtifacts.find((a) => a.id === p.id)) {
            newArtifacts = [...newArtifacts, p];
          }
        }

        return prev.map((t) =>
          t.runId === runId
            ? {
                ...t,
                status: statusUpdate || t.status,
                events: [...(t.events || []), ev],
                artifacts: newArtifacts,
                completedAt:
                  statusUpdate === "completed" || statusUpdate === "failed" || statusUpdate === "stopped"
                    ? Date.now()
                    : t.completedAt,
              }
            : t
        );
      });
    });

    return unsub;
  }, []);

  // Create a new task (called when user triggers a workflow)
  const createTask = useCallback((workflowId, inputObj, sessionId) => {
    const manifest = getManifest(workflowId);
    const taskId = `task-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const runId = sessionId || taskId;

    const task = {
      id: taskId,
      runId,
      workflowId,
      workflowName: manifest?.name || workflowId,
      status: "pending",
      startedAt: Date.now(),
      completedAt: null,
      input: inputObj,
      sessionId,
      events: [],
      artifacts: [],
    };

    setTasks((prev) => [task, ...prev]);
    setSelectedTaskId(taskId);

    // Send the actual command via chat
    if (onSend) {
      const envelope = {
        agent_name: workflowId,
        input: inputObj || {},
        thread_id: sessionId || undefined,
      };
      const text = `请调用 langgraph_agent 工具完成任务：\n${JSON.stringify(envelope)}`;
      onSend(text);

      // Mark as running after a short delay (gives time for backend to pick up)
      setTimeout(() => {
        setTasks((prev) =>
          prev.map((t) =>
            t.id === taskId ? { ...t, status: "running" } : t
          )
        );
      }, 500);
    }

    return taskId;
  }, [onSend]);

  // Stop a task
  const stopTask = useCallback((taskId) => {
    setTasks((prev) =>
      prev.map((t) =>
        t.id === taskId ? { ...t, status: "stopped", completedAt: Date.now() } : t
      )
    );
    if (onStop) onStop();
  }, [onStop]);

  // Clear completed tasks
  const clearCompleted = useCallback(() => {
    setTasks((prev) => prev.filter((t) => !(t.status === "completed" || t.status === "failed" || t.status === "stopped")));
    if (selectedTaskId) {
      setSelectedTaskId("");
    }
  }, [selectedTaskId]);

  // Clear ALL tasks (for cleaning up stale / test data)
  const clearAll = useCallback(() => {
    setTasks([]);
    setSelectedTaskId("");
  }, []);

  return {
    tasks,
    selectedTaskId,
    onSelectTask: setSelectedTaskId,
    createTask,
    stopTask,
    clearCompleted,
    clearAll,
  };
}
