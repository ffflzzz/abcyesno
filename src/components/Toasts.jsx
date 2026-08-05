import React, { useEffect, useState, useRef } from "react";
import Icon from "./Icon.jsx";
import { onToastShow, onToastClear } from "../hooks/uiBus.js";

/**
 * Toasts — 通用通知层（P1）。
 * 订阅 uiBus 的 notification.show / notification.clear 事件，渲染右上角堆叠 toast。
 * - level: info | success | warn | error
 * - kind: sticky（需手动关闭）| ttl（定时自动消失，ttlMs 控制）
 * - key:   notification.clear 按 key 关闭对应 toast
 */
const LEVEL_ICON = {
  info: "info",
  success: "check-circle",
  warn: "warning",
  error: "warning",
};

let autoId = 0;

export default function Toasts() {
  const [toasts, setToasts] = useState([]);
  const timers = useRef(new Map());

  useEffect(() => {
    const offShow = onToastShow((p) => {
      const id = p.key || `toast-${++autoId}`;
      const ttl = typeof p.ttlMs === "number" ? p.ttlMs : p.kind === "sticky" ? 0 : 4000;
      setToasts((prev) => {
        const next = prev.filter((t) => t.id !== id);
        return [...next, { id, level: p.level || "info", text: p.text || "", ttl }];
      });
      if (timers.current.has(id)) {
        clearTimeout(timers.current.get(id));
        timers.current.delete(id);
      }
      if (ttl > 0) {
        const timer = setTimeout(() => dismiss(id), ttl);
        timers.current.set(id, timer);
      }
    });
    const offClear = onToastClear((p) => {
      if (p && p.key) dismiss(p.key);
    });
    return () => {
      offShow();
      offClear();
      timers.current.forEach((t) => clearTimeout(t));
      timers.current.clear();
    };
  }, []);

  function dismiss(id) {
    if (timers.current.has(id)) {
      clearTimeout(timers.current.get(id));
      timers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.level}`}>
          <Icon name={LEVEL_ICON[t.level] || "info"} size={15} />
          <span className="toast-text">{t.text}</span>
          <button className="toast-close" onClick={() => dismiss(t.id)} title="关闭">
            <Icon name="close" size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
