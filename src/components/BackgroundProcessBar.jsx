import React, { useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import {
  barLabel,
  displayCommand,
  exitLabel,
  finishedLabel,
  formatUptime,
  isStale,
  lastOutputLine,
  pickLiveliest,
  pickVisible,
  shortCommand,
  silentLabel,
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
  // 心跳：折叠态也要说清"最后一次有新输出是多久前"。今天最坑的就是后端静默
  // 25 分钟、界面上完全看不出区别。
  const liveliest = pickLiveliest(running, (p) => p._silentSeconds || 0);
  const silent = liveliest ? liveliest._silentSeconds || 0 : 0;
  const stale = anyRunning && isStale(silent);
  // 折叠态也要交代结果：只说"已结束"会把"跑完"和"挂了"混成一个词。
  const label = anyRunning
    ? `${barLabel(running)} · ${silentLabel(silent)}`
    : finishedLabel(justFinished);
  // 折叠态给出"最新一行输出"——取最近还在动的那个，不是跑得最久的那个。
  const tailSource = liveliest || rows[0];
  const tailLine = lastOutputLine(
    tailSource && (tailSource.output_tail || tailSource.output_preview)
  );

  return (
    <div
      className={`bpm-bar ${stale ? "is-stale" : anyRunning ? "is-running" : "is-idle"}`}
      data-testid="background-process-bar"
    >
      <button
        type="button"
        className="bpm-header"
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起后台任务" : "展开后台任务"}
      >
        <span className={`bpm-dot ${anyRunning && !stale ? "bpm-dot-pulse" : ""}`} />
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
                  {isRun ? (
                    <span className={`bpm-chip ${isStale(p._silentSeconds || 0) ? "c-stale" : ""}`}>
                      {silentLabel(p._silentSeconds || 0)}
                    </span>
                  ) : null}
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
