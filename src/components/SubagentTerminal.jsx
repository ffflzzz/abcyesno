import React, { useState, useEffect, useRef } from "react";
import Icon from "./Icon.jsx";

/**
 * SubagentTerminal — 子智能体终端风格工作日志
 *
 * 替代旧的 SubagentPanel（子智能体大列表 + WorkflowGraphPanel 节点动画）。
 * langgraph agent（漫剧/论文等 workflow.* 事件）的工作过程以**等宽终端日志流**
 * 呈现：逐行追加、自动滚底、游标闪烁；产物以紧凑 chip 列出（点击预览）。
 * 非 langgraph 的 subagent 退化为单行状态（目标 + 状态 + 当前工具）。
 */
function artifactSrc(a) {
  if (!a) return null;
  return a.url || a.src || "";
}

export default function SubagentTerminal({ subagents = [], onOpenPreviewUrl }) {
  const [open, setOpen] = useState(true);
  const endRef = useRef(null);

  const lang = subagents.find((s) => s.key === "__langgraph__");
  const others = subagents.filter((s) => s.key !== "__langgraph__");
  const log = (lang && lang.log) || [];
  const artifacts = (lang && lang.artifacts) || [];
  const status = (lang && lang.status) || (subagents.length ? "running" : "");
  const isDone = status === "complete" || status === "error";

  useEffect(() => {
    if (open && endRef.current) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [log.length, open]);

  if (subagents.length === 0) return null;

  return (
    <div className={`subagent-terminal ${isDone ? "ended" : ""}`}>
      <button className="subagent-terminal-toggle" onClick={() => setOpen((o) => !o)}>
        <Icon name="terminal" size={13} />
        <span className="subagent-terminal-label">子智能体</span>
        {lang ? (
          <span className={`subagent-terminal-status ${status}`}>
            {status === "error" ? "失败" : isDone ? "完成" : "运行中"}
            {lang.total > 1 && lang.episode ? ` · 第 ${lang.episode}/${lang.total} 集` : ""}
          </span>
        ) : (
          <span className="subagent-terminal-count">{subagents.length}</span>
        )}
        <Icon name="chevron" size={12} style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }} />
      </button>
      {open && (
        <div className="subagent-terminal-body">
          {lang && (
            <div className="subagent-terminal-log">
              {log.map((line, i) => (
                <div key={i} className="subagent-terminal-line">
                  {line}
                </div>
              ))}
              {!isDone && <span className="terminal-cursor">▌</span>}
              <div ref={endRef} />
            </div>
          )}
          {artifacts.length > 0 && (
            <div className="subagent-terminal-artifacts">
              {artifacts.map((a, i) => {
                const src = artifactSrc(a);
                return (
                  <button
                    key={a.id || `art-${i}`}
                    className="subagent-artifact-chip"
                    title={`${a.label}${src ? "（点击预览）" : ""}`}
                    onClick={() => {
                      if (src && onOpenPreviewUrl) onOpenPreviewUrl(src);
                    }}
                    disabled={!src || !onOpenPreviewUrl}
                  >
                    <Icon name={a.type === "video" ? "play" : "image"} size={11} />
                    <span className="subagent-artifact-chip-label">{a.label}</span>
                  </button>
                );
              })}
            </div>
          )}
          {others.map((s) => {
            const st = s.status || (s.event || "").replace("subagent.", "") || "running";
            return (
              <div className="subagent-simple-row" key={s.key}>
                <span className={`subagent-status subagent-status-${st}`}>{st}</span>
                <span className="subagent-goal" title={s.goal}>{s.goal || s.key}</span>
                {s.tool_name && <span className="subagent-tool">🔧 {s.tool_name}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
