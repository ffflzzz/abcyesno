import React, { useEffect, useRef } from "react";
import Icon from "./Icon.jsx";

/**
 * ConfirmModal — 暗色主题确认弹窗（替代原生 window.confirm）
 * props: { open, title, message, confirmText?, cancelText?, danger?, onConfirm, onClose }
 */
export default function ConfirmModal({
  open,
  title = "确认",
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  onConfirm,
  onClose,
}) {
  const ref = useRef(null);

  // Auto-focus confirm button on open; trap focus inside modal
  useEffect(() => {
    if (open) {
      const btn = ref.current?.querySelector(".modal-confirm");
      btn?.focus();
      // ESC closes
      const handler = (e) => { if (e.key === "Escape") onClose?.(); };
      window.addEventListener("keydown", handler);
      return () => window.removeEventListener("keydown", handler);
    }
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-mask" onClick={onClose} ref={ref}>
      <div className="modal modal-confirm-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-confirm-header">
          <Icon name={danger ? "warning" : "info"} size={18} />
          <h3>{title}</h3>
        </div>
        <p className="modal-confirm-body">{message}</p>
        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>
            {cancelText}
          </button>
          <button
            className={`modal-confirm ${danger ? "danger" : "primary"}`}
            onClick={() => { onConfirm?.(); onClose?.(); }}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
