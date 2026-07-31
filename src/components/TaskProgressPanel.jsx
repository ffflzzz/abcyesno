import React, { useState, useMemo } from "react";
import Icon from "./Icon.jsx";

/**
 * TaskProgressPanel — 实时任务进度面板
 *
 * 显示 agent 当前工作流的步骤列表（○ 状态指示器）。
 * 数据来源：
 *   1. 工具调用消息（tool role）→ 每个工具是一个步骤
 *   2. thinkingText 解析出的步骤行 → 补充非工具步骤
 *
 * 步骤状态：
 *   - pending:  尚未开始（○ 灰色）
 *   - active:   正在执行（● 脉冲动画 + accent 色）
 *   - done:     已完成（✓ 绿色）
 *   - error:    出错（✗ 红色）
 */

function StepIcon({ status }) {
  switch (status) {
    case "active":
      return <span className="step-icon step-icon-active" aria-hidden="true" />;
    case "done":
      return <span className="step-icon step-icon-done" aria-hidden="true"><Icon name="check" size={12} /></span>;
    case "error":
      return <span className="step-icon step-icon-error" aria-hidden="true"><Icon name="close" size={12} /></span>;
    default:
      return <span className="step-icon step-icon-pending" aria-hidden="true" />;
  }
}

/**
 * 从消息列表 + thinkingText 构建步骤列表
 */
function buildSteps(messages, thinkingText, isLoading) {
  const steps = [];
  let lastToolIndex = -1;

  // 1. 工具调用 → 步骤
  for (const m of messages) {
    if (m.role === "tool") {
      lastToolIndex++;
      let label = m.toolName || "tool";
      // 截断过长参数描述
      const argsSnippet = (m.args || "").replace(/\s+/g, " ").slice(0, 60);
      if (argsSnippet && argsSnippet !== "{}") {
        label += ` (${argsSnippet})`;
      }

      let status = "pending";
      if (m.status === "running" || m.status === "in_progress") status = "active";
      else if (m.status === "complete" || m.status === "done") status = "done";
      else if (m.status === "error") status = "error";

      steps.push({
        id: m.id || `tool-${lastToolIndex}`,
        type: "tool",
        label,
        status,
        hasArtifact: hasImageResult(m),
      });
    }
  }

  // 2. 如果正在加载且最后一步是 active，保持 active
  if (isLoading && steps.length > 0) {
    const last = steps[steps.length - 1];
    if (last.status === "pending") {
      last.status = "active";
    }
  }

  // 3. 从 thinkingText 提取当前正在做的步骤描述（作为"当前步骤"补充）
  if (isLoading && thinkingText) {
    const currentAction = extractCurrentAction(thinkingText);
    if (currentAction && (!steps.length || steps[steps.length - 1].status === "active")) {
      // 只有当最后一步已经是 active 时，才追加或更新当前动作描述
      const existing = steps.find(s => s.status === "active");
      if (existing) {
        existing.actionDetail = currentAction;
      } else {
        steps.push({
          id: `thinking-${Date.now()}`,
          type: "thinking",
          label: currentAction,
          status: "active",
        });
      }
    }
  }

  return steps;
}

/** 检查工具结果是否含图片数据 */
function hasImageResult(msg) {
  const content = msg.result || msg.content || "";
  if (typeof content !== "string") return false;
  // base64 image or URL with image extension
  return (
    /^data:image\//i.test(content) ||
    /\.(png|jpe?g|gif|svg|webp|bmp)(\?|$)/i.test(content.trim())
  );
}

/** 从 thinkingText 提取当前动作描述（取最后一行有意义的文本） */
function extractCurrentAction(text) {
  if (!text) return null;
  const lines = text.trim().split("\n").filter(l => l.trim());
  if (lines.length === 0) return null;
  // 取最后一行，截断到合理长度
  const last = lines[lines.length - 1].trim();
  return last.slice(0, 80);
}

export default function TaskProgressPanel({ messages = [], thinkingText = "", isLoading = false }) {
  const [expanded, setExpanded] = useState(true);

  const steps = useMemo(
    () => buildSteps(messages, thinkingText, isLoading),
    [messages, thinkingText, isLoading]
  );

  // 无步骤时不渲染
  if (steps.length === 0) return null;

  const doneCount = steps.filter(s => s.status === "done").length;
  const activeCount = steps.filter(s => s.status === "active").length;
  const summary = isLoading
    ? `进行中 ${activeCount > 0 ? `· ${activeCount} 活跃` : ""} · ${doneCount}/${steps.length} 完成`
    : `${steps.length} 个步骤 · ${doneCount} 完成`;

  return (
    <div className="task-progress">
      <button
        className="task-progress-header"
        onClick={() => setExpanded(!expanded)}
        type="button"
      >
        <span className="task-progress-title">概览</span>
        <span className="task-progress-summary">{summary}</span>
        <span className={`task-progress-chevron ${expanded ? "open" : ""}`} aria-hidden="true">
          <Icon name="chevron" size={12} style={{ transform: expanded ? "rotate(90deg)" : "rotate(180deg)" }} />
        </span>
      </button>

      {expanded && (
        <div className="task-progress-body">
          <div className="task-progress-section">
            <div className="task-progress-section-title">任务进程</div>
            <div className="task-steps">
              {steps.map((step) => (
                <div key={step.id} className={`task-step task-step-${step.status}`}>
                  <StepIcon status={step.status} />
                  <span className="task-step-label">{step.label}</span>
                  {step.hasArtifact && (
                    <span className="task-step-badge" title="包含图片产物"><Icon name="image" size={12} /></span>
                  )}
                  {step.actionDetail && step.status === "active" && (
                    <span className="task-step-detail">{step.actionDetail}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
