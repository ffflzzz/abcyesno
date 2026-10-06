import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";

// 「Key 池」编辑弹窗（写 AGNES_API_KEYS）。
//
// 语法与短剧侧 `v5/config.py::_split_keys_with_base` 一致：
//   每条 = key[@专属地址][#视频rpm[:图片rpm]]，逗号分隔、首条优先。
// 已存的 key 明文只在主进程 —— 这里只拿到掩码，行里留空 = 不改（用 keep 序号引用）。
// 池里第一条 = 主 Key（对话/工作台跟它走），首条变了主进程会自动重启对话后台。

function toRow(e) {
  return {
    keep: e.idx,
    masked: e.masked || "",
    key: "",
    base: e.base || "",
    videoRpm: e.videoRpm || "",
    imageRpm: e.imageRpm || "",
  };
}

const BLANK = { keep: null, masked: "", key: "", base: "", videoRpm: "", imageRpm: "" };

export default function KeyPoolModal({ onClose, onSaved }) {
  const [rows, setRows] = useState(null);
  const [saving, setSaving] = useState(false);
  const [rowErrors, setRowErrors] = useState({});
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const h = window.hermes;
    if (!h || !h.getApiKeyPool) {
      setError("这版应用没有 Key 池接口（重启应用加载新版）");
      setRows([]);
      return () => { alive = false; };
    }
    h.getApiKeyPool()
      .then((s) => {
        if (!alive) return;
        const entries = (s && s.entries) || [];
        setRows(entries.length ? entries.map(toRow) : [{ ...BLANK }]);
      })
      .catch((err) => {
        if (!alive) return;
        setError(err && err.message ? err.message : String(err));
        setRows([]);
      });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !saving) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  function patch(i, field, value) {
    setRowErrors((m) => (m[i] ? { ...m, [i]: "" } : m));
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: value } : r)));
  }

  function addRow() {
    setRows((rs) => [...rs, { ...BLANK }]);
  }

  function removeRow(i) {
    setRowErrors({});
    setRows((rs) => rs.filter((_, j) => j !== i));
  }

  async function handleSave() {
    if (saving || !rows) return;
    setError("");
    setRowErrors({});
    setSaving(true);
    try {
      const res = await window.hermes.setApiKeyPool(rows.map((r) => ({
        keep: r.keep,
        key: r.key,
        base: r.base,
        videoRpm: r.videoRpm,
        imageRpm: r.imageRpm,
      })));
      if (!res || !res.success) {
        const failures = (res && res.failures) || [];
        if (failures.length) {
          const m = {};
          failures.forEach((f) => { m[f.row] = f.error; });
          setRowErrors(m);
          setError("有几条 key 没通过校验，按行看下面的原因");
        } else {
          setError((res && res.error) || "保存失败");
        }
        return;
      }
      onSaved(res);
    } catch (err) {
      setError(err && err.message ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const realRows = (rows || []).filter((r) => r.keep !== null || r.key.trim());
  const canSave = !!rows && !saving && realRows.length > 0;

  return (
    <div className="modal-mask" onClick={() => { if (!saving) onClose(); }}>
      <div className="modal kpm" onClick={(e) => e.stopPropagation()}>
        <h3><Icon name="key" size={16} /> Key 池（多条轮换）</h3>
        <p className="modal-desc">
          按顺序使用。<b>第一条是主 Key</b>，对话和其他应用都跟它走；多条会轮换提交
          —— 视频按每条自己的 rpm 并行，填了图片 rpm 的 key 还会被静帧优先拿去用。
        </p>

        <div className="kpm-rows">
          {(rows || []).map((r, i) => {
            const hasVideo = !!String(r.videoRpm).trim();
            return (
              <div className="kpm-row" key={i}>
                <div className="kpm-row-head">
                  <span className="kpm-row-idx">{i === 0 ? "主 Key" : `第 ${i + 1} 条`}</span>
                  {rows.length > 1 && (
                    <button className="kpm-del" onClick={() => removeRow(i)} disabled={saving}>删除</button>
                  )}
                </div>
                <input
                  className="kpm-input"
                  type="password"
                  autoComplete="off"
                  placeholder={r.masked ? `已存 ${r.masked}，不改就留空` : "粘贴 API Key…"}
                  value={r.key}
                  onChange={(e) => patch(i, "key", e.target.value)}
                />
                {rowErrors[i] ? <div className="modal-error">{rowErrors[i]}</div> : null}
                <div className="kpm-grid">
                  <label>
                    专属地址（可选）
                    <input
                      className="kpm-input"
                      type="text"
                      placeholder="留空 = 默认入口"
                      value={r.base}
                      onChange={(e) => patch(i, "base", e.target.value)}
                    />
                  </label>
                  <label>
                    视频 rpm
                    <input
                      className="kpm-input"
                      type="number"
                      min="1"
                      placeholder="默认 1"
                      value={r.videoRpm}
                      onChange={(e) => patch(i, "videoRpm", e.target.value)}
                    />
                  </label>
                  <label>
                    图片 rpm
                    <input
                      className="kpm-input"
                      type="number"
                      min="1"
                      disabled={!hasVideo}
                      title={hasVideo ? "" : "先填视频 rpm（池语法是 #视频[:图片]）"}
                      placeholder="不参与生图"
                      value={r.imageRpm}
                      onChange={(e) => patch(i, "imageRpm", e.target.value)}
                    />
                  </label>
                </div>
              </div>
            );
          })}
        </div>

        <button className="ghost kpm-add" onClick={addRow} disabled={saving}>+ 再加一条</button>

        {error ? <div className="modal-error">{error}</div> : null}

        <div className="modal-actions">
          <button className="ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="primary" onClick={handleSave} disabled={!canSave}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
