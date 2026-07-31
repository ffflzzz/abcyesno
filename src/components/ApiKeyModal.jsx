import React, { useState } from "react";
import Icon from "./Icon.jsx";

export default function ApiKeyModal({ onSave, onClose }) {
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSave() {
    if (!key.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      const result = await window.hermes.validateApiKey(key.trim());
      if (!result || !result.valid) {
        setError(result && result.error ? result.error : "API Key 无效，请检查后重试。");
        return;
      }
      await onSave(key.trim());
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3><Icon name="key" size={16} /> 设置 API Key</h3>
        <p className="modal-desc">输入你的 API Key，程序会安全保存在本地。</p>
        <input
          type="password"
          autoFocus
          placeholder="粘贴 API Key…"
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            if (error) setError("");
          }}
          onKeyDown={(e) => e.key === "Enter" && handleSave()}
        />
        {error && <div className="modal-error">{error}</div>}
        <div className="modal-actions">
          <button className="ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="primary" disabled={!key.trim() || saving} onClick={handleSave}>
            {saving ? "验证中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
