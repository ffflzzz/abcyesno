import React, { useState, useEffect, useRef } from "react";
import Icon from "./Icon.jsx";

/**
 * AgentVerboseTimeline
 * ------------------------------------------------------------------
 * Codex / Claude-Code 风格的 Agent 执行时间线组件：
 *   - 垂直时间线：左侧状态图标 + 连接线，右侧步骤卡片
 *   - 步骤类型：thought（思考）/ tool（工具）/ result（结果）/ system（系统）
 *   - 状态：pending（等待）/ running（执行中）/ complete（完成）/ error（失败）
 *   - running 步骤带呼吸/旋转动画；步骤可单独展开/折叠；头部可展开/收起全部
 *   - 纯渲染组件：steps（VerboseStep[]）由 MessageThread 负责构建，本组件不改动事件协议
 *
 * 不引入任何新的运行时依赖（图标用 Unicode + CSS 动画）。
 */

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const STATUS_LABEL = {
  pending: "等待中",
  running: "执行中",
  complete: "完成",
  error: "失败",
};

const TYPE_LABEL = {
  thought: "思考",
  tool: "工具",
  result: "结果",
  system: "系统",
};

function StepIcon({ type, status }) {
  const cls = ["av-step-icon"];
  let name;
  if (type === "tool") {
    if (status === "running") {
      name = "settings";
      cls.push("av-spinning");
    } else if (status === "error") {
      name = "close";
    } else {
      name = "check-circle";
    }
    cls.push("av-type-tool");
  } else if (type === "thought") {
    name = "brain";
    cls.push("av-type-thought");
    if (status === "running") cls.push("av-breathing");
  } else if (type === "result") {
    name = "note";
    cls.push("av-type-result");
  } else {
    name = "info";
    cls.push("av-type-system");
  }
  if (status === "error" && type !== "tool") cls.push("av-status-error-icon");
  return <div className={cls.join(" ")}><Icon name={name} size={14} /></div>;
}

function renderDetails(details) {
  if (details === undefined || details === null || details === "") return null;
  if (typeof details === "object") {
    return <pre className="av-code">{JSON.stringify(details, null, 2)}</pre>;
  }
  return <pre className="av-code">{String(details)}</pre>;
}

function StepDetails({ step, isOpen }) {
  const isError = step.type === "result" && step.status === "error";
  const meta = step.metadata;
  return (
    <div className={`av-details ${isError ? "error-text" : ""} ${isOpen ? "open" : ""}`}>
      {renderDetails(step.details)}
      {meta && (
        <div className="av-meta">
          {meta.filePath && (
            <span>
              <b>文件</b> {meta.filePath}
            </span>
          )}
          {meta.command && (
            <span>
              <b>命令</b> {meta.command}
            </span>
          )}
          {(meta.linesAdded !== undefined || meta.linesRemoved !== undefined) && (
            <span>
              <b>+{meta.linesAdded ?? 0}</b> / <b>-{meta.linesRemoved ?? 0}</b>
            </span>
          )}
          {meta.exitCode !== undefined && (
            <span>
              <b>退出码</b> {meta.exitCode}
            </span>
          )}
          {meta.errorMessage && (
            <span className="error-text">
              <b>错误</b> {meta.errorMessage}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function StepCard({ step, isLast, isOpen, onToggle }) {
  const statusCls = `av-status-${step.status}`;
  return (
    <div className={`av-step ${statusCls} ${isOpen ? "open" : ""}`}>
      {!isLast && <span className="av-connector" />}
      <StepIcon type={step.type} status={step.status} />
      <div className="av-card" onClick={() => onToggle(step.id)}>
        <div className="av-card-head">
          <span className="av-type-label">{TYPE_LABEL[step.type] || step.type}</span>
          {step.name && <span className="av-name">{step.name}</span>}
          <span className="av-status-tag">{STATUS_LABEL[step.status] || step.status}</span>
          {step.createdAt && <span className="av-time">{formatTime(step.createdAt)}</span>}
        </div>
        {step.summary && <div className="av-summary">{step.summary}</div>}
        <StepDetails step={step} isOpen={isOpen} />
      </div>
    </div>
  );
}

export default function AgentVerboseTimeline({ steps = [], onStepClick, onToggleCollapse }) {
  const [openIds, setOpenIds] = useState(() => {
    const init = {};
    steps.forEach((s) => {
      init[s.id] = s.status === "error";
    });
    return init;
  });
  const [allOpen, setAllOpen] = useState(false);
  const endRef = useRef(null);

  // Newly arrived steps default to collapsed, except errors (open for visibility).
  useEffect(() => {
    setOpenIds((prev) => {
      let changed = false;
      const next = { ...prev };
      steps.forEach((s) => {
        if (!(s.id in next)) {
          next[s.id] = s.status === "error";
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [steps]);

  // Auto-scroll to the latest step while something is still running.
  useEffect(() => {
    if (steps.some((s) => s.status === "running") && endRef.current) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [steps]);

  const toggle = (id) => {
    setOpenIds((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      if (onStepClick) onStepClick(id, next[id]);
      return next;
    });
  };

  const toggleAll = () => {
    const nextAll = !allOpen;
    setAllOpen(nextAll);
    const next = {};
    steps.forEach((s) => {
      next[s.id] = nextAll;
    });
    setOpenIds(next);
    if (onToggleCollapse) onToggleCollapse(nextAll);
  };

  if (!steps || steps.length === 0) return null;

  return (
    <div className="agent-verbose">
      <div className="av-header">
        <span className="av-title">Agent 执行过程 · {steps.length} 步</span>
        <button type="button" className="av-toggle-all" onClick={toggleAll}>
          {allAllLabel(allOpen)}
        </button>
      </div>
      <div className="av-steps" ref={endRef}>
        {steps.map((step, i) => (
          <StepCard
            key={step.id}
            step={step}
            isLast={i === steps.length - 1}
            isOpen={!!openIds[step.id]}
            onToggle={toggle}
          />
        ))}
      </div>
    </div>
  );
}

function allAllLabel(allOpen) {
  return allOpen ? "收起全部" : "展开全部";
}
