import React from "react";

function formatDate(ts) {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "-";
  return `${d.getFullYear()}-${(d.getMonth() + 1).toString().padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

export default function DetailModal({ type, data, onClose }) {
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>查看详情</h3>
        <div className="detail-list">
          {type === "assistant" && (
            <>
              <div className="detail-row"><span className="detail-key">ID</span><span className="detail-value">{data.id}</span></div>
              <div className="detail-row"><span className="detail-key">名称</span><span className="detail-value">{data.name}</span></div>
              <div className="detail-row"><span className="detail-key">Skill ID</span><span className="detail-value">{data.skillId}</span></div>
              <div className="detail-row"><span className="detail-key">默认模型</span><span className="detail-value">{data.defaultModel || "-"}</span></div>
              <div className="detail-row"><span className="detail-key">能力</span><span className="detail-value">{(data.capabilities || []).join(", ") || "-"}</span></div>
              <div className="detail-row"><span className="detail-key">创建时间</span><span className="detail-value">{formatDate(data.createdAt)}</span></div>
            </>
          )}
          {type === "session" && (
            <>
              <div className="detail-row"><span className="detail-key">ID</span><span className="detail-value">{data.id}</span></div>
              <div className="detail-row"><span className="detail-key">助手 ID</span><span className="detail-value">{data.assistantId}</span></div>
              <div className="detail-row"><span className="detail-key">标题</span><span className="detail-value">{data.title}</span></div>
              <div className="detail-row"><span className="detail-key">创建时间</span><span className="detail-value">{formatDate(data.createdAt)}</span></div>
              <div className="detail-row"><span className="detail-key">更新时间</span><span className="detail-value">{formatDate(data.updatedAt)}</span></div>
              <div className="detail-row"><span className="detail-key">消息数</span><span className="detail-value">{(data.messages || []).length}</span></div>
            </>
          )}
        </div>
        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
