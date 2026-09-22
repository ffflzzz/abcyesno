import React, { useState, useEffect } from "react";
import Icon from "./Icon.jsx";
import { useTts } from "../hooks/useTts.jsx";

function maskKey(key) {
  if (!key) return "";
  if (key.length <= 8) return "****";
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
}

export default function SettingsPanel({ apiKey = "", hasApiKey = false, apiKeys = null, keyStatus = "", model = "", theme = "dark", onThemeChange, onEditApiKey, onClearApiKey, onClose, version = "", onOpenWechatBind }) {
  const [openDirStatus, setOpenDirStatus] = useState("");
  const [updater, setUpdater] = useState(null);
  const { ttsSettings, updateTtsSettings, voiceOptions } = useTts();
  const { autoRead, voice, rate } = ttsSettings;

  // 主 Key 显示值：优先用主进程返回的掩码快照，回退到旧 props。
  const mainDisplay = apiKeys && apiKeys.main
    ? (apiKeys.main.set ? apiKeys.main.masked : "未设置")
    : (apiKey ? maskKey(apiKey) : hasApiKey ? "已设置" : "未设置");

  // 图片/视频 Key 行：有覆盖显示掩码，否则显示「跟随对话 Key」。
  function scopedDisplay(scope) {
    const s = apiKeys && apiKeys[scope];
    if (!s) return "跟随对话 Key";
    return s.set ? s.masked : "跟随对话 Key";
  }

  useEffect(() => {
    setOpenDirStatus("");
  }, []);

  // 自动更新状态订阅：主进程推送 state 快照（supported/status/progress/…）。
  // 仅 NSIS 安装版 supported=true；dev/绿色版为 null/false → 按钮走旧版
  // 「打开 Releases 页」行为。
  useEffect(() => {
    const h = window.hermes;
    if (!h || !h.getUpdaterState) return undefined;
    let alive = true;
    h.getUpdaterState().then((s) => { if (alive) setUpdater(s); }).catch(() => {});
    const onState = (s) => setUpdater(s);
    h.onUpdaterState(onState);
    return () => {
      alive = false;
      h.offUpdaterState(onState);
    };
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

  // 检查更新：
  // - NSIS 安装版（updater.supported=true）→ 应用内检查 + 后台下载 + 应用内重启安装；
  // - dev/绿色解压版 → 保持旧行为：打开 GitHub Releases 页（系统浏览器），手动下载覆盖。
  function handleCheckUpdate() {
    if (updater && updater.supported) {
      window.hermes.checkForUpdate().catch(() => {});
      return;
    }
    const url = "https://github.com/ffflzzz/abcyesno/releases";
    if (window.hermes && window.hermes.openExternal) {
      window.hermes.openExternal(url);
    } else {
      window.open(url, "_blank", "noopener");
    }
  }

  // 「重启更新」：退出并安装已下载的新版本（quitAndInstall 由主进程处理）。
  function handleInstallUpdate() {
    if (window.hermes && window.hermes.installUpdate) {
      window.hermes.installUpdate().catch(() => {});
    }
  }

  // 「关于 Abcyesno」条目的文案，随更新状态机变化。
  function aboutDesc() {
    const base = `Abcyesno ${version ? `v${version}` : "v-dev"} · 便携桌面 Agent 平台`;
    if (!updater || !updater.supported) return base;
    const u = updater;
    const newV = u.info && u.info.version ? `v${u.info.version}` : "新版本";
    switch (u.status) {
      case "checking": return "正在检查更新…";
      case "uptodate": return `${base} · 已是最新`;
      case "downloading":
        return u.progress
          ? `正在下载 ${newV}… ${u.progress.percent}%`
          : `发现 ${newV}，正在下载…`;
      case "downloaded": return `${newV} 已就绪，点击「重启更新」安装（数据不受影响）`;
      case "error": return `更新失败：${u.error || "未知错误"}`;
      default: return base;
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3>设置</h3>
          <button className="settings-close" onClick={onClose} title="关闭"><Icon name="close" size={14} /></button>
        </div>

        {/* API 密钥：一个主 Key 全端通用，图片/视频/备用可按需单独覆盖 */}
        <div className="settings-group">
          <div className="settings-group-title">API 密钥</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">对话（LLM）</div>
              <div className="settings-item-desc">主 Key，所有应用共用；保存在本机，保存后重启后台。</div>
            </div>
            <div className="settings-item-control">
              <span className="settings-value">{mainDisplay}</span>
              <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("main")}>
                {mainDisplay !== "未设置" ? "修改" : "设置"}
              </button>
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">图片生成</div>
              <div className="settings-item-desc">不填则跟随对话 Key。适合给创作类应用单独隔离额度。</div>
            </div>
            <div className="settings-item-control">
              <span className="settings-value">{scopedDisplay("image")}</span>
              <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("image")}>
                {apiKeys && apiKeys.image && apiKeys.image.set ? "修改" : "覆盖"}
              </button>
              {apiKeys && apiKeys.image && apiKeys.image.set && (
                <button className="ghost settings-inline-btn" onClick={() => onClearApiKey("image")}>清除</button>
              )}
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">视频生成</div>
              <div className="settings-item-desc">不填则跟随对话 Key；覆盖后下次生成任务生效。</div>
            </div>
            <div className="settings-item-control">
              <span className="settings-value">{scopedDisplay("video")}</span>
              <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("video")}>
                {apiKeys && apiKeys.video && apiKeys.video.set ? "修改" : "覆盖"}
              </button>
              {apiKeys && apiKeys.video && apiKeys.video.set && (
                <button className="ghost settings-inline-btn" onClick={() => onClearApiKey("video")}>清除</button>
              )}
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">备用 Key</div>
              <div className="settings-item-desc">对话 Key 额度耗尽（429）时降级使用，可选。</div>
            </div>
            <div className="settings-item-control">
              <span className="settings-value">{scopedDisplay("fallback")}</span>
              <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("fallback")}>
                {apiKeys && apiKeys.fallback && apiKeys.fallback.set ? "修改" : "设置"}
              </button>
              {apiKeys && apiKeys.fallback && apiKeys.fallback.set && (
                <button className="ghost settings-inline-btn" onClick={() => onClearApiKey("fallback")}>清除</button>
              )}
            </div>
          </div>
          {keyStatus && <div className="settings-status-error">{keyStatus}</div>}
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

        {/* 语音朗读 */}
        <div className="settings-group">
          <div className="settings-group-title">语音朗读</div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">自动朗读</div>
              <div className="settings-item-desc">收到助手回复后自动朗读（需联网；云端 edge-tts 中文语音）。</div>
            </div>
            <div className="settings-item-control">
              <div className="settings-seg">
                <button
                  className={`settings-seg-btn ${!autoRead ? "active" : ""}`}
                  onClick={() => updateTtsSettings({ autoRead: false })}
                >关闭</button>
                <button
                  className={`settings-seg-btn ${autoRead ? "active" : ""}`}
                  onClick={() => updateTtsSettings({ autoRead: true })}
                >开启</button>
              </div>
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">音色</div>
              <div className="settings-item-desc">微软云端中文神经语音（晓晓 / 云希 等）。</div>
            </div>
            <div className="settings-item-control">
              <select
                className="modal-select"
                value={voice}
                onChange={(e) => updateTtsSettings({ voice: e.target.value })}
              >
                {voiceOptions.map((v) => (
                  <option key={v.value} value={v.value}>{v.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="settings-item">
            <div className="settings-item-text">
              <div className="settings-item-name">语速</div>
              <div className="settings-item-desc">朗读速度，1.0 为正常语速。</div>
            </div>
            <div className="settings-item-control" style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <input
                type="range"
                min="0.5"
                max="2"
                step="0.1"
                value={rate}
                onChange={(e) => updateTtsSettings({ rate: Number(e.target.value) })}
                style={{ flex: 1, accentColor: "var(--accent, #4f7cff)" }}
              />
              <span className="settings-value" style={{ minWidth: 36, textAlign: "right" }}>{rate.toFixed(1)}×</span>
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
              <div className="settings-item-desc">{aboutDesc()}</div>
              {updater && updater.supported && updater.status === "downloading" && updater.progress && (
                <div
                  style={{
                    marginTop: 8,
                    width: "100%",
                    maxWidth: 320,
                    height: 6,
                    borderRadius: 3,
                    background: "rgba(127,127,127,0.25)",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${Math.min(100, updater.progress.percent || 0)}%`,
                      height: "100%",
                      borderRadius: 3,
                      background: "#4f8cff",
                      transition: "width 0.3s ease",
                    }}
                  />
                </div>
              )}
            </div>
            <div className="settings-item-control">
              {updater && updater.supported && updater.status === "downloaded" ? (
                <button className="primary" onClick={handleInstallUpdate}>重启更新</button>
              ) : (
                <button
                  className="ghost"
                  onClick={handleCheckUpdate}
                  disabled={updater && updater.supported && (updater.status === "checking" || updater.status === "downloading")}
                >
                  {updater && updater.supported && updater.status === "error" ? "重试" : "检查更新"}
                </button>
              )}
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
