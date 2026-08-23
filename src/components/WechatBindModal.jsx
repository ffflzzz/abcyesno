import React, { useState, useEffect } from "react";
import Icon from "./Icon.jsx";

/**
 * Modal for binding/unbinding WeChat and viewing bridge status.
 * - Unbound: show a QR (fetched from the bridge) + refresh button + status.
 * - Bound: show masked account id + unbind + test-message + log peek.
 *
 * The QR itself is rendered from a data URL produced by the main process
 * (qrcode package), so we never touch temp PNG files on personal dirs.
 */
export default function WechatBindModal({ onClose }) {
  const [status, setStatus] = useState({ state: "idle", bound: false });
  const [qr, setQr] = useState(null);
  const [loadingQr, setLoadingQr] = useState(false);
  const [logs, setLogs] = useState([]);
  const [logsOpen, setLogsOpen] = useState(false); 
  const [testText, setTestText] = useState("");
  const [testResult, setTestResult] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const s = await window.hermes.wechatCall("getStatus");
      setStatus(s.result || s || {});
    } catch (e) {
      setStatus({ state: "error", detail: e.message });
    }
  }

  async function fetchQr() {
    setLoadingQr(true);
    try {
      const res = await window.hermes.wechatCall("getQrCode");
      const payload = res?.result || {};
      setQr(payload.qrDataUrl || null);
      setStatus({ ...status, state: payload.qrDataUrl ? "awaiting_qr" : "error" });
    } catch (e) {
      setStatus({ state: "error", detail: e.message });
    } finally {
      setLoadingQr(false);
    }
  }

  useEffect(() => {
    refresh();
    const unsub = window.hermes.onWechatStatus((s) => {
      setStatus(s);
      if (s.state === "awaiting_qr") fetchQr();
      if (s.state === "connected" || s.state === "error") setQr(null);
    });
    return () => { try { unsub(); } catch { /* ignore */ } }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleUnbind() {
    if (busy) return;
    setBusy(true);
    try {
      await window.hermes.wechatCall("unbind");
      setQr(null);
      refresh();
    } finally { setBusy(false); }
  }

  async function handleTest() {
    if (!testText.trim()) return;
    const res = await window.hermes.wechatCall("sendTestMessage", { text: testText.trim() });
    setTestResult(
      res?.result
        ? (res.result.ok ? "✅ 已发送" : `发送失败：${res.result.error}`)
        : (res.ok ? "" : `失败：${res.error}`)
    );
    setTestText("");
  }

  async function loadLogs() {
    try {
      const res = await window.hermes.wechatCall("getLogs", { lines: 80 });
      setLogs((res?.result?.lines || []).slice(-30));
    } catch { /* ignore */ }
  }

  const stateMap = {
    idle: { label: "未绑定", cls: "offline" },
    awaiting_qr: { label: "等待扫码", cls: "connecting" },
    qr_expired: { label: "二维码过期", cls: "connecting" },
    connecting: { label: "连接中", cls: "connecting" },
    connected: { label: "已连接", cls: "online" },
    reconnecting: { label: "重连中", cls: "connecting" },
    error: { label: "异常", cls: "offline" },
  };
  const sMeta = stateMap[status.state] || { label: status.state, cls: "offline" };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3><Icon name="wechat" size={16} /> 微信绑定</h3>
          <button className="settings-close" onClick={onClose} title="关闭"><Icon name="close" size={14} /></button>
        </div>

        <div className="wx-status-row">
          <span className={`status-dot ${sMeta.cls}`} />
          <span className="wx-status-label">{sMeta.label}</span>
          {status.accountMasked && <span className="wx-account">账号 {status.accountMasked}</span>}
        </div>
        {status.detail && <div className="settings-status-error">{status.detail}</div>}

        {!status.bound && (
          <div className="wx-qr-block">
            {qr ? (
              <img className="wx-qr" src={qr} alt="微信扫码绑定" />
            ) : (
              <div className="wx-qr-placeholder">
                {loadingQr ? "正在生成二维码…" : "点击下方按钮获取二维码"}
              </div>
            )}
            <p className="modal-desc">使用微信扫描上方二维码完成绑定；绑定后，微信里发消息即可调用 Abcyesno 默认对话。</p>
            {(status.state === "awaiting_qr" || !qr) && (
              <button className="primary" disabled={loadingQr} onClick={fetchQr}>
                {qr ? "刷新二维码" : "获取二维码"}
              </button>
            )}
          </div>
        )}

        {status.bound && (
          <div className="wx-bound-block">
            <div className="wx-row">
              <input
                type="text"
                placeholder="向微信发送测试消息…"
                value={testText}
                onChange={(e) => setTestText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleTest()}
              />
              <button className="primary" onClick={handleTest} disabled={!testText.trim()}>发送</button>
            </div>
            {testResult && <div className="wx-test-result">{testResult}</div>}

            <button className="ghost wx-logs-toggle" onClick={() => { setLogsOpen((v) => !v); if (!logs.length) loadLogs(); }}>
              {logsOpen ? "隐藏运行日志" : "查看运行日志"}
            </button>
            {logsOpen && (
              <pre className="wx-logs">{logs.join("\n") || "（暂无日志）"}</pre>
            )}

            <button className="ghost danger-text" disabled={busy} onClick={handleUnbind}>解绑微信</button>
          </div>
        )}

        <div className="modal-actions">
          <button className="primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
