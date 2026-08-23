import React, { useState, useEffect } from "react";
import Icon from "./Icon.jsx";

function maskKey(key) {
  if (!key) return "";
  if (key.length <= 8) return "****";
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
}

export default function SettingsPanel({ apiKey = "", hasApiKey = false, model = "", theme = "dark", onThemeChange, onEditApiKey, onClose, version = "", onOpenWechatBind }) {
  const [openDirStatus, setOpenDirStatus] = useState("");

  useEffect(() => {
    setOpenDirStatus("");
  }, []);

  async function handleOpenDevTools() {
    // Close the settings modal first so keyboard focus returns to the main
    // window and the renderer-side F12 listener can later toggle DevTools off.
    if (onClose) onClose();
    if (window.hermes && window.hermes.openDevTools) {
      try { await window.hermes.openDevTools(); } catch (err) { console.error("openDevTools failed", err); }
    }
  }

  async function handleQuit() {
    if (window.hermes && window.hermes.quitApp) {
      try { await window.hermes.quitApp(); } catch (err) { console.error("quitApp failed", err); }
    }
  }

  async function handleOpenDataDir() {
    setOpenDirStatus("");
    if (!window.hermes || !window.hermes.openDataDir) {
      setOpenDirStatus("暂不支持打开数据目录");
      return;
    }
    try {
      const result = await window.hermes.openDataDir();
      if (!result || !result.success) {
        setOpenDirStatus((result && result.error) || "打开失败");
      }
    } catch (err) {
      setOpenDirStatus(err && err.message ? err.message : String(err));
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3>设置</h3>
          <button className="settings-close" onClick={onClose} title="关闭"><Icon name="close" size={14} /></button>
        </div>

        {/* 账号 */}
        <div className="settings-group">
          <div className="settings-group-title">账号</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">API Key</div>
              <div className="settings-item-desc">用于连接 Agnes 模型的密钥，仅保存在本机。</div>
            </div>
            <div className="settings-item-control">
              <span className="settings-value">
                {apiKey ? maskKey(apiKey) : hasApiKey ? "已设置" : "未设置"}
              </span>
              <button className="ghost settings-inline-btn" onClick={onEditApiKey}>
                {apiKey || hasApiKey ? "修改" : "设置"}
              </button>
            </div>
          </div>
        </div>

        {/* 模型 */}
        <div className="settings-group">
          <div className="settings-group-title">模型</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">默认模型</div>
              <div className="settings-item-desc">新会话默认使用的模型，可在输入框下方临时切换。</div>
            </div>
            <div className="settings-item-control">
              <span className="settings-value">{model || "未选择"}</span>
            </div>
          </div>
        </div>

        {/* 外观 */}
        <div className="settings-group">
          <div className="settings-group-title">外观</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">主题</div>
              <div className="settings-item-desc">选择界面配色，跟随系统则随操作系统明暗切换。</div>
            </div>
            <div className="settings-item-control">
              <div className="settings-seg">
                {[
                  { value: "dark", label: "深色" },
                  { value: "light", label: "浅色" },
                  { value: "system", label: "跟随系统" },
                ].map((opt) => (
                  <button
                    key={opt.value}
                    className={`settings-seg-btn ${theme === opt.value ? "active" : ""}`}
                    onClick={() => onThemeChange && onThemeChange(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* 数据 */}
        <div className="settings-group">
          <div className="settings-group-title">数据</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">数据目录</div>
              <div className="settings-item-desc">会话、助手与配置存放的本地文件夹。</div>
            </div>
            <div className="settings-item-control">
              <button className="ghost" onClick={handleOpenDataDir}>打开数据目录</button>
            </div>
          </div>
          {openDirStatus && <div className="settings-status-error">{openDirStatus}</div>}
        </div>

        {/* 微信绑定 */}
        <div className="settings-group">
          <div className="settings-group-title">微信</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">微信桥接</div>
              <div className="settings-item-desc">把个人微信接入 Abcyesno，在微信里直接发消息调用默认对话。</div>
            </div>
            <div className="settings-item-control">
              <button className="ghost" onClick={onOpenWechatBind}>绑定 / 管理</button>
            </div>
          </div>
        </div>

        {/* 高级（原原生菜单栏的功能迁移至此） */}
        <div className="settings-group">
          <div className="settings-group-title">高级</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">开发控制台</div>
              <div className="settings-item-desc">打开/关闭开发者工具（F12；若 F12 被系统占用，请用 Ctrl+Shift+I）。</div>
            </div>
            <div className="settings-item-control">
              <button className="ghost" onClick={handleOpenDevTools}>切换</button>
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">关于 Abcyesno</div>
              <div className="settings-item-desc">Abcyesno {version ? `v${version}` : "v1.3.0"} · 便携桌面 Agent 平台</div>
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">退出应用</div>
              <div className="settings-item-desc">关闭并退出 Abcyesno。</div>
            </div>
            <div className="settings-item-control">
              <button className="ghost danger-text" onClick={handleQuit}>退出</button>
            </div>
          </div>
        </div>

        <div className="modal-actions">
          <button className="primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
