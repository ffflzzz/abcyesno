import React, { useState } from "react";
import Icon from "./Icon.jsx";

// scope: "main"（对话 Key，保存后重启 Hermes）| "image" | "video" | "fallback"
// scoped key 保存只写本地配置、即时生效（下次调用读新值），无需重启。
const SCOPE_META = {
  main: {
    title: "设置对话 API Key",
    desc: "用于对话模型（所有应用共用）。保存后会重启后台服务。",
  },
  image: {
    title: "设置图片生成 Key",
    desc: "不填则自动跟随对话 Key。保存后即时生效。",
    emptyHint: "留空保存 = 清除覆盖，恢复跟随对话 Key。",
  },
  video: {
    title: "设置视频生成 Key",
    desc: "不填则自动跟随对话 Key。正在运行的任务仍用旧值，下次生成生效。",
    emptyHint: "留空保存 = 清除覆盖，恢复跟随对话 Key。",
  },
  fallback: {
    title: "设置备用 Key",
    desc: "对话 Key 额度耗尽（429）时自动降级使用的公网 Key，可留空。",
    emptyHint: "留空保存 = 清除备用 Key。",
  },
};

export default function ApiKeyModal({ onSave, onClose, scope = "main", allowEmpty = false }) {
  const meta = SCOPE_META[scope] || SCOPE_META.main;
  const canBeEmpty = allowEmpty && scope !== "main";
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSave() {
    const val = key.trim();
    if ((!val && !canBeEmpty) || saving) return;
    setSaving(true);
    setError("");
    try {
      if (val) {
        const result = await window.hermes.validateApiKey(val);
        if (!result || !result.valid) {
          setError(result && result.error ? result.error : "API Key 无效，请检查后重试。");
          return;
        }
      }
      try {
        await onSave(val);
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
        return;
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3><Icon name="key" size={16} /> {meta.title}</h3>
        <p className="modal-desc">{meta.desc}</p>
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
          <button
            className="primary"
            disabled={(!key.trim() && !canBeEmpty) || saving}
            onClick={handleSave}
          >
            {saving ? "验证中…" : key.trim() ? "保存" : "清除"}
          </button>
        </div>
        {canBeEmpty && meta.emptyHint && (
          <p className="modal-desc" style={{ marginTop: 6, opacity: 0.75 }}>{meta.emptyHint}</p>
        )}
      </div>
    </div>
  );
}
