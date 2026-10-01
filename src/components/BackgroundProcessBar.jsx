import React, { useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import {
  barLabel,
  displayCommand,
  exitLabel,
  finishedLabel,
  formatUptime,
  lastOutputLine,
  pickVisible,
  shortCommand,
} from "../utils/backgroundProcesses.js";

/**
 * 后台进程常驻条
 *
 * 存在意义（2026-10-01）：agent 用 terminal 后台跑一小时的活时，界面上
 * 回合早已结束、输入框回到"输入问题"、停止按钮消失 —— 和完全空闲长得一样。
 * 这条常驻显示"几个在跑 / 已多久 / 最后一行输出"，跑完那一刻变成"跑完/被终止"。
 *
 * 只读：停止走网关的 process.kill，不在这个 MVP 里做，避免误杀用户别的会话的进程。
 */
export default function BackgroundProcessBar({
  running = [],
  justFinished = [],
  onRefresh,
  onDismiss,
}) {
  const [open, setOpen] = useState(false);
  const rows = useMemo(() => pickVisible(running, justFinished), [running, justFinished]);

  if (rows.length === 0) return null;

  const anyRunning = running.length > 0;
  // 折叠态也要交代结果：只说"已结束"会把"跑完"和"挂了"混成一个词。
  const label = anyRunning ? barLabel(running) : finishedLabel(justFinished);
  // 折叠态也给出"最新一行输出"，这样不展开也能看出它确实在动而不是卡住。
  const newest = [...rows].sort(
    (a, b) => (Number(b.uptime_seconds) || 0) - (Number(a.uptime_seconds) || 0)
  )[0];
  const tailLine = lastOutputLine(newest && (newest.output_tail || newest.output_preview));

  return (
    <div
      className={`bpm-bar ${anyRunning ? "is-running" : "is-idle"}`}
      data-testid="background-process-bar"
    >
      <button
        type="button"
        className="bpm-header"
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起后台任务" : "展开后台任务"}
      >
        <span className={`bpm-dot ${anyRunning ? "bpm-dot-pulse" : ""}`} />
        <span className="bpm-label">{label}</span>
        {!open && tailLine ? <span className="bpm-tail">{shortCommand(tailLine, 60)}</span> : null}
        <span className="bpm-spacer" />
        {onRefresh ? (
          <span
            className="bpm-icon-btn"
            role="button"
            tabIndex={-1}
            title="立即刷新"
            onClick={(e) => {
              e.stopPropagation();
              onRefresh();
            }}
          >
            <Icon name="refresh" size={14} />
          </span>
        ) : null}
        {onDismiss && !anyRunning ? (
          <span
            className="bpm-icon-btn"
            role="button"
            tabIndex={-1}
            title="不再显示"
            onClick={(e) => {
              e.stopPropagation();
              onDismiss();
            }}
          >
            <Icon name="close" size={14} />
          </span>
        ) : null}
        <span className={`bpm-caret ${open ? "is-open" : ""}`}>
          <Icon name="chevron" size={14} />
        </span>
      </button>

      {open ? (
        <div className="bpm-list">
          {rows.map((p) => {
            const isRun = p.status !== "exited";
            const line = lastOutputLine(p.output_tail || p.output_preview);
            return (
              <div key={p.session_id} className={`bpm-row ${isRun ? "row-running" : "row-exited"}`}>
                <pre className="bpm-cmd-full" title={p.command}>
                  {displayCommand(p.command)}
                </pre>
                <div className="bpm-row-sub">
                  <span className={`bpm-chip bpm-state ${isRun ? "c-running" : "c-exited"}`}>
                    {isRun ? `已跑 ${formatUptime(p.uptime_seconds)}` : exitLabel(p)}
                  </span>
                  {p.pid ? <span className="bpm-chip">PID {p.pid}</span> : null}
                  {p.started_at ? <span className="bpm-chip">{p.started_at}</span> : null}
                  {p.cwd ? <span className="bpm-chip bpm-cwd" title={p.cwd}>{p.cwd}</span> : null}
                </div>
                {line ? <pre className="bpm-out">{line}</pre> : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
