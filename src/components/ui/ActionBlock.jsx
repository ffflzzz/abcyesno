import React from "react";

/**
 * ActionBlock — 操作实时预览 (spec §5.5)
 * props: {
 *   type: 'file_write'|'command'|'http_request'|'generic',
 *   status: 'pending'|'running'|'done'|'error',
 *   target?: string,        // 文件路径 / URL / 命令摘要
 *   preview?: string,       // 实时内容预览（流式追加）
 *   previewLang?: string,
 *   detail?: string,        // 底部状态文字
 *   error?: string,         // status=error 时的错误
 * }
 */
const TYPE_LABEL = {
  file_write: "📝 写入文件",
  command: "⌨ 执行命令",
  http_request: "🌐 请求",
  generic: "⚡ 操作",
};

const STATUS_CLASS = {
  pending: "ui-act-pending",
  running: "ui-act-running",
  done: "ui-act-done",
  error: "ui-act-error",
};

const STATUS_ICON = {
  pending: "○",
  running: "●",
  done: "✓",
  error: "✕",
};

function openTarget(target) {
  const hermes = typeof window !== "undefined" ? window.hermes : null;
  if (!target || !hermes?.openExternal) return;
  // 本地路径用 file:// 协议委托系统打开，URL 直接打开
  if (/^(https?:)?\/\//i.test(target) || target.startsWith("file://")) {
    hermes.openExternal(target);
  } else {
    hermes.openExternal("file://" + target);
  }
}

export default function ActionBlock({
  type = "generic",
  status = "pending",
  target,
  preview,
  previewLang,
  detail,
  error,
}) {
  const isFile = type === "file_write";
  const isUrl =
    typeof target === "string" && /^(https?:)?\/\//i.test(target);
  const clickable = !!target && (isFile || isUrl || /^[A-Za-z]:[\\/]|\//.test(target || ""));
  const cls = STATUS_CLASS[status] || "ui-act-pending";

  return (
    <div className={`ui-block ui-action ${cls}`}>
      <div className="ui-action-head">
        <span className="ui-action-status" aria-hidden>
          {STATUS_ICON[status] || "○"}
        </span>
        <span className="ui-action-type">
          {TYPE_LABEL[type] || TYPE_LABEL.generic}
        </span>
        {target && (
          <button
            type="button"
            className={`ui-action-target ${clickable ? "clickable" : ""}`}
            title={clickable ? "点击打开" : target}
            onClick={clickable ? () => openTarget(target) : undefined}
            disabled={!clickable}
          >
            {target}
          </button>
        )}
      </div>

      {preview != null && String(preview).length > 0 && (
        <div className={`ui-action-preview${previewLang ? " lang-" + previewLang : ""}`}>
          <pre>{preview}</pre>
        </div>
      )}

      {status === "error" && error && (
        <div className="ui-action-error">⚠ {error}</div>
      )}

      {detail && <div className="ui-action-detail">{detail}</div>}
    </div>
  );
}
