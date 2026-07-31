import React, { useState, useEffect, useRef } from "react";
import Icon from "./Icon.jsx";

// ── DevConsole: captures window.console output and displays in a panel ──
// Intercepts console.log/warn/error/info and shows timestamps.

const LEVEL_STYLES = {
  log: { color: "var(--text)", prefix: "clipboard" },
  info: { color: "var(--accent)", prefix: "info" },
  warn: { color: "var(--warning)", prefix: "warning" },
  error: { color: "var(--error-text)", prefix: "close" },
};

export default function DevConsole({ open, onClose }) {
  const [entries, setEntries] = useState([]);
  const [filter, setFilter] = useState(""); // log|warn|error|info|all
  const bodyRef = useRef(null);
  const entriesRef = useRef([]);

  // Intercept console methods when open
  useEffect(() => {
    if (!open) return;

    const orig = { log: console.log, warn: console.warn, error: console.error, info: console.info };
    const levels = ["log", "warn", "error", "info"];

    const makeInterceptor = (level) => (...args) => {
      const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      const msg = args.map((a) => {
        if (typeof a === "string") return a;
        try { return JSON.stringify(a); }
        catch { return String(a); }
      }).join(" ");

      const entry = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, level, ts, msg };
      entriesRef.current = [entry, ...entriesRef.current].slice(0, 500); // cap at 500
      setEntries([...entriesRef.current]);

      // Also call original so devtools still works
      orig[level](...args);
    };

    levels.forEach((l) => { console[l] = makeInterceptor(l); });

    // Seed with welcome message
    setTimeout(() => {
      const entry = { id: "welcome", level: "info", ts: new Date().toLocaleTimeString("zh-CN", { hour12: false }), msg: "开发控制台已开启 — console.log/warn/error/output 将显示在这里" };
      entriesRef.current = [entry, ...entriesRef.current];
      setEntries([...entriesRef.current]);
    }, 0);

    return () => {
      levels.forEach((l) => { console[l] = orig[l]; });
    };
  }, [open]);

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (bodyRef.current && open) {
      bodyRef.current.scrollTop = 0; // newest first → scroll to top
    }
  }, [entries.length, open]);

  const filtered = filter
    ? entries.filter((e) => e.level === filter)
    : entries;

  const clearEntries = () => {
    entriesRef.current = [];
    setEntries([]);
  };

  if (!open) return null;

  return (
    <div className="dev-console-overlay">
      <div className="dev-console">
        <div className="dev-console-header">
          <span className="dev-console-title"><Icon name="monitor" size={16} /> 开发控制台</span>
          <div className="dev-console-actions">
            <div className="dev-console-filters">
              {["all", "log", "info", "warn", "error"].map((l) => (
                <button
                  key={l}
                  className={`dev-filter-btn ${filter === l ? "active" : ""}`}
                  onClick={() => setFilter(filter === l ? "" : l)}
                >
                  {l === "all" ? "全部" : l.toUpperCase()}
                </button>
              ))}
            </div>
            <button className="dev-console-btn" onClick={clearEntries} title="清空">
              <Icon name="trash" size={14} />
            </button>
            <button className="dev-console-btn" onClick={onClose} title="关闭">
              <Icon name="close" size={14} />
            </button>
          </div>
        </div>
        <div className="dev-console-body" ref={bodyRef}>
          {filtered.length === 0 ? (
            <div className="dev-empty">暂无日志</div>
          ) : (
            filtered.map((e) => {
              const style = LEVEL_STYLES[e.level] || LEVEL_STYLES.log;
              return (
                <div key={e.id} className={`dev-log-row level-${e.level}`}>
                  <span className="dev-log-ts">{e.ts}</span>
                  <span className="dev-log-prefix"><Icon name={style.prefix} size={12} /></span>
                  <span className="dev-log-msg" style={{ color: style.color }}>{e.msg}</span>
                </div>
              );
            })
          )}
        </div>
        <div className="dev-console-footer">
          <span>{filtered.length} 条记录</span>
          <span>实时拦截 console.log/warn/error</span>
        </div>
      </div>
    </div>
  );
}
