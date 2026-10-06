import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import LocalMediaCard from "./LocalMediaCard.jsx";
import KeyPoolModal from "./KeyPoolModal.jsx";
import { useTts } from "../hooks/useTts.jsx";

const CATEGORIES = [
  { id: "connection", label: "API 与模型", icon: "key", hint: "各类密钥额度与默认模型" },
  { id: "appearance", label: "外观", icon: "palette", hint: "界面配色" },
  { id: "voice", label: "语音朗读", icon: "audio", hint: "自动朗读、音色与语速" },
  { id: "data", label: "数据", icon: "folder-open", hint: "本地文件位置" },
  { id: "integrations", label: "集成", icon: "wechat", hint: "外部消息通道" },
  { id: "advanced", label: "高级", icon: "wrench", hint: "控制台、更新与退出" },
];

export default function SettingsPanel({ apiKeys = null, keyStatus = "", model = "", theme = "dark", onThemeChange, onEditApiKey, onClearApiKey, onClose, version = "", onOpenWechatBind }) {
  const [openDirStatus, setOpenDirStatus] = useState("");
  const [updater, setUpdater] = useState(null);
  const [activeCat, setActiveCat] = useState(CATEGORIES[0].id);
  const [query, setQuery] = useState("");
  // Key 池（AGNES_API_KEYS）：掩码快照 + 编辑弹窗 + 保存后的生效提示。
  const [pool, setPool] = useState(null);
  const [showPool, setShowPool] = useState(false);
  const [poolNote, setPoolNote] = useState("");
  const [restarting, setRestarting] = useState(false);
  const [localMediaOpen, setLocalMediaOpen] = useState(false);
  const paneRef = useRef(null);
  const { ttsSettings, updateTtsSettings, voiceOptions } = useTts();
  const { autoRead, voice, rate } = ttsSettings;

  const has = (scope) => !!(apiKeys && apiKeys[scope] && apiKeys[scope].set);
  const masked = (scope) => (apiKeys && apiKeys[scope] ? apiKeys[scope].masked : "");

  function refreshPool() {
    const h = window.hermes;
    if (!h || !h.getApiKeyPool) return;
    h.getApiKeyPool().then(setPool).catch(() => {});
  }

  useEffect(() => { refreshPool(); }, []);

  function handlePoolSaved(res) {
    setShowPool(false);
    refreshPool();
    setPoolNote(res && res.mainChanged
      ? "已保存；主 Key 变了，对话后台已自动重启。"
      : "已保存。");
  }

  async function handleRestartShortdrama() {
    const h = window.hermes;
    if (!h || !h.restartShortdrama || restarting) return;
    if (!window.confirm("重启会打断正在跑的短剧任务，确定重启短剧后台？")) return;
    setRestarting(true);
    try {
      const r = await h.restartShortdrama();
      if (!r || r.ok === false) {
        setPoolNote(`重启失败：${(r && r.error) || "未知错误"}`);
      } else if (r.started) {
        setPoolNote("短剧后台已重启，新 Key 池已生效。");
      } else {
        setPoolNote("短剧后台没在跑 —— 下次打开短剧工厂时就用的新 Key。");
      }
    } catch (err) {
      setPoolNote(`重启失败：${err && err.message ? err.message : String(err)}`);
    } finally {
      setRestarting(false);
    }
  }

  const poolEntries = (pool && pool.entries) || [];

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

  useEffect(() => {
    // 池编辑弹窗开着时 Esc 只关它（两个 window 级监听都会收到按键）。
    const onKey = (e) => { if (e.key === "Escape" && !showPool) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, showPool]);

  useEffect(() => {
    if (paneRef.current) paneRef.current.scrollTop = 0;
  }, [activeCat, query]);

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

  // ── 设置项登记表：导航、搜索与内容区都由这一份数据驱动 ──────────────────
  // 每项 { id, cat, name, desc, kw, badge, value, control }
  const items = [
    {
      id: "key-pool",
      cat: "connection",
      name: "Key 池（对话 + 短剧工厂）",
      desc: "第一条是主 Key（对话与其他应用共用）；多条 key 轮换提交，视频与生图按各自的 rpm 并行。国内 key 可带专属地址与限速。",
      kw: "api key 密钥 池 keypool 多条 轮换 主key 对话 llm 地址 rpm 短剧工厂",
      badge: poolEntries.length ? { tone: "ok", text: `${poolEntries.length} 条` } : { tone: "warn", text: "未设置" },
      value: poolEntries.length ? poolEntries[0].masked : "",
      control: (
        <button className="ghost settings-inline-btn" onClick={() => setShowPool(true)}>
          {poolEntries.length ? "编辑" : "设置"}
        </button>
      ),
      after: (
        <div className="keypool">
          {poolEntries.length > 0 && (
            <div className="keypool-list">
              {poolEntries.map((e, i) => (
                <div className="keypool-row" key={e.idx}>
                  <span className="keypool-idx">{i === 0 ? "主" : i + 1}</span>
                  <span className="keypool-key">{e.masked}</span>
                  {e.base ? <span className="keypool-meta">{e.base.replace(/^https?:\/\//i, "")}</span> : null}
                  {e.videoRpm ? <span className="keypool-meta">视频 {e.videoRpm}rpm</span> : null}
                  {e.imageRpm ? <span className="keypool-meta">图片 {e.imageRpm}rpm</span> : null}
                </div>
              ))}
            </div>
          )}
          {poolNote ? (
            <div className="keypool-note">
              <span>{poolNote}</span>
              {poolEntries.length > 0 && (
                <button
                  className="ghost settings-inline-btn"
                  title="让短剧后台重读 Key；正在跑的短剧任务会中断"
                  onClick={handleRestartShortdrama}
                  disabled={restarting}
                >
                  {restarting ? "重启中…" : "重启短剧后台"}
                </button>
              )}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      id: "key-image",
      cat: "connection",
      name: "图片生成",
      desc: "不填则跟随对话 Key。只作用于工作台与漫剧的图片调用 —— 短剧工厂走上面的 Key 池。",
      kw: "api key 密钥 图片 生图 image 工作台 漫剧",
      badge: has("image") ? { tone: "ok", text: "独立 Key" } : { tone: "muted", text: "跟随主 Key" },
      value: has("image") ? masked("image") : "",
      control: (
        <>
          <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("image")}>
            {has("image") ? "修改" : "设置"}
          </button>
          {has("image") && (
            <button className="ghost settings-inline-btn" onClick={() => onClearApiKey("image")}>清除</button>
          )}
        </>
      ),
    },
    {
      id: "key-video",
      cat: "connection",
      name: "视频生成",
      desc: "不填则跟随对话 Key。只作用于工作台与漫剧的视频调用 —— 短剧工厂走上面的 Key 池。",
      kw: "api key 密钥 视频 生视频 video 工作台 漫剧",
      badge: has("video") ? { tone: "ok", text: "独立 Key" } : { tone: "muted", text: "跟随主 Key" },
      value: has("video") ? masked("video") : "",
      control: (
        <>
          <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("video")}>
            {has("video") ? "修改" : "设置"}
          </button>
          {has("video") && (
            <button className="ghost settings-inline-btn" onClick={() => onClearApiKey("video")}>清除</button>
          )}
        </>
      ),
    },
    {
      id: "key-fallback",
      cat: "connection",
      name: "备用 Key",
      desc: "对话 Key 额度耗尽（429）时降级使用，可选。只作用于对话与工作台 —— 短剧工厂靠池内多条 key 轮换。",
      kw: "api key 密钥 备用 fallback 429 额度",
      badge: has("fallback") ? { tone: "ok", text: "已设置" } : { tone: "muted", text: "未设置" },
      value: has("fallback") ? masked("fallback") : "",
      control: (
        <>
          <button className="ghost settings-inline-btn" onClick={() => onEditApiKey("fallback")}>
            {has("fallback") ? "修改" : "设置"}
          </button>
          {has("fallback") && (
            <button className="ghost settings-inline-btn" onClick={() => onClearApiKey("fallback")}>清除</button>
          )}
        </>
      ),
    },
    {
      id: "local-media",
      cat: "connection",
      name: "本地出片服务",
      desc: "探一探这台机（或你填的另一台机器）上跑着的 ComfyUI，把出片的视频从云端切成它。静帧仍走云端。",
      kw: "comfyui 本地 模型 探测 接入 出片 视频 显卡 minimax h3 ollama",
      control: (
        <button className="ghost settings-inline-btn" onClick={() => setLocalMediaOpen((v) => !v)}>
          {localMediaOpen ? "收起" : "展开"}
        </button>
      ),
      // 常挂不卸载：面板里只有按按钮才会起后台进程（见 LocalMediaCard 文件头），
      // 折起来只是 display:none，探测结果不会因为收起再展开而丢失。
      after: (
        <div style={localMediaOpen ? undefined : { display: "none" }}>
          <LocalMediaCard />
        </div>
      ),
    },
    {
      id: "model-default",
      cat: "connection",
      name: "默认模型",
      desc: "新会话默认使用的模型，可在输入框下方临时切换。",
      kw: "模型 model agnes 默认",
      value: model || "未选择",
    },
    {
      id: "theme",
      cat: "appearance",
      name: "主题",
      desc: "选择界面配色，跟随系统则随操作系统明暗切换。",
      kw: "主题 theme 深色 浅色 外观 配色 暗色",
      control: (
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
      ),
    },
    {
      id: "tts-auto",
      cat: "voice",
      name: "自动朗读",
      desc: "收到助手回复后自动朗读（需联网；云端 edge-tts 中文语音）。",
      kw: "语音 tts 朗读 自动 播报",
      control: (
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
      ),
    },
    {
      id: "tts-voice",
      cat: "voice",
      name: "音色",
      desc: "微软云端中文神经语音（晓晓 / 云希 等）。",
      kw: "语音 音色 声音 voice 晓晓 云希",
      control: (
        <select
          className="settings-select"
          value={voice}
          onChange={(e) => updateTtsSettings({ voice: e.target.value })}
        >
          {voiceOptions.map((v) => (
            <option key={v.value} value={v.value}>{v.label}</option>
          ))}
        </select>
      ),
    },
    {
      id: "tts-rate",
      cat: "voice",
      name: "语速",
      desc: "朗读速度，1.0 为正常语速。",
      kw: "语音 语速 rate 速度 快慢",
      value: `${rate.toFixed(1)}×`,
      control: (
        <input
          className="settings-range"
          type="range"
          min="0.5"
          max="2"
          step="0.1"
          value={rate}
          onChange={(e) => updateTtsSettings({ rate: Number(e.target.value) })}
        />
      ),
    },
    {
      id: "data-dir",
      cat: "data",
      name: "数据目录",
      desc: "会话、助手与配置存放的本地文件夹。",
      kw: "数据 目录 文件夹 路径 存储 data dir",
      control: <button className="ghost settings-inline-btn" onClick={handleOpenDataDir}>打开数据目录</button>,
      after: openDirStatus ? <div className="settings-status-error">{openDirStatus}</div> : null,
    },
    {
      id: "wechat",
      cat: "integrations",
      name: "微信桥接",
      desc: "把个人微信接入 Abcyesno，在微信里直接发消息调用默认对话。",
      kw: "微信 wechat 桥接 集成 绑定 机器人",
      control: <button className="ghost settings-inline-btn" onClick={onOpenWechatBind}>绑定 / 管理</button>,
    },
    {
      id: "devtools",
      cat: "advanced",
      name: "开发控制台",
      desc: "打开/关闭开发者工具（F12；若 F12 被系统占用，请用 Ctrl+Shift+I）。",
      kw: "开发 devtools 控制台 调试 日志",
      control: <button className="ghost settings-inline-btn" onClick={handleOpenDevTools}>切换</button>,
    },
    {
      id: "about",
      cat: "advanced",
      name: "关于 Abcyesno",
      desc: aboutDesc(),
      kw: "关于 版本 更新 version update",
      control: (
        updater && updater.supported && updater.status === "downloaded"
          ? <button className="primary settings-inline-btn" onClick={handleInstallUpdate}>重启更新</button>
          : (
            <button
              className="ghost settings-inline-btn"
              onClick={handleCheckUpdate}
              disabled={updater && updater.supported && (updater.status === "checking" || updater.status === "downloading")}
            >
              {updater && updater.supported && updater.status === "error" ? "重试" : "检查更新"}
            </button>
          )
      ),
      after: updater && updater.supported && updater.status === "downloading" && updater.progress ? (
        <div className="settings-progress">
          <div className="settings-progress-fill" style={{ width: `${Math.min(100, updater.progress.percent || 0)}%` }} />
        </div>
      ) : null,
    },
    {
      id: "quit",
      cat: "advanced",
      name: "退出应用",
      desc: "关闭并退出 Abcyesno。",
      kw: "退出 quit 关闭 离开",
      danger: true,
      control: <button className="ghost danger-text settings-inline-btn" onClick={handleQuit}>退出</button>,
    },
  ];

  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (it) => {
    if (!tokens.length) return true;
    const cat = CATEGORIES.find((c) => c.id === it.cat);
    const hay = `${it.name} ${it.desc} ${it.kw || ""} ${cat ? cat.label : ""}`.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  };
  // 每次渲染重算：items 里带着当前 state 的闭包，缓存会让搜索结果中的控件失效。
  const matched = items.filter(matches);

  // 搜索时：命中项按分类分组呈现，导航只点亮有命中的分类。
  const searching = tokens.length > 0;
  const hitCats = new Set(matched.map((it) => it.cat));
  const shownItems = searching ? matched : items.filter((it) => it.cat === activeCat);
  const shownCat = CATEGORIES.find((c) => c.id === activeCat) || CATEGORIES[0];

  function renderCard(it) {
    return (
      <div key={it.id} className={`settings-card ${it.danger ? "danger" : ""}`}>
        <div className="settings-card-text">
          <div className="settings-card-name">
            {it.name}
            {it.badge && <span className={`settings-badge ${it.badge.tone}`}>{it.badge.text}</span>}
          </div>
          <div className="settings-card-desc">{it.desc}</div>
          {it.after}
        </div>
        <div className="settings-card-control">
          {it.value && <span className="settings-value">{it.value}</span>}
          {it.control}
        </div>
      </div>
    );
  }

  // 搜索结果按分类插小标题，保持与导航一致的层级。
  const blocks = [];
  let lastCat = null;
  shownItems.forEach((it) => {
    if (searching && it.cat !== lastCat) {
      lastCat = it.cat;
      const c = CATEGORIES.find((x) => x.id === it.cat);
      blocks.push(<div key={`h-${it.cat}`} className="settings-pane-subhead">{c ? c.label : it.cat}</div>);
    }
    blocks.push(renderCard(it));
  });

  return (
    <>
    <div className="modal-mask" onClick={onClose}>
      <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3>设置</h3>
          <button className="settings-close" onClick={onClose} title="关闭（Esc）"><Icon name="close" size={14} /></button>
        </div>

        <div className="settings-body">
          <nav className="settings-nav">
            <label className="settings-search">
              <Icon name="search" size={13} />
              <input
                type="text"
                placeholder="搜索设置项…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {searching && (
                <button className="settings-search-clear" onClick={() => setQuery("")} title="清除搜索">
                  <Icon name="close" size={12} />
                </button>
              )}
            </label>
            <div className="settings-nav-list">
              {CATEGORIES.map((c) => {
                const n = matched.filter((it) => it.cat === c.id).length;
                const dim = searching && n === 0;
                return (
                  <button
                    key={c.id}
                    className={`settings-nav-item ${!searching && activeCat === c.id ? "active" : ""} ${dim ? "dim" : ""}`}
                    onClick={() => { setQuery(""); setActiveCat(c.id); }}
                    title={c.hint}
                  >
                    <Icon name={c.icon} size={15} />
                    <span className="settings-nav-label">{c.label}</span>
                    {searching && n > 0 && <span className="settings-nav-count">{n}</span>}
                  </button>
                );
              })}
            </div>
            <div className="settings-nav-foot">Abcyesno {version ? `v${version}` : "v-dev"}</div>
          </nav>

          <section className="settings-pane" ref={paneRef}>
            <div className="settings-pane-head">
              <h4>{searching ? "搜索结果" : shownCat.label}</h4>
              <p>
                {searching
                  ? (matched.length ? `匹配到 ${matched.length} 项设置` : "没有匹配的设置，换个关键词试试。")
                  : shownCat.hint}
              </p>
            </div>
            {blocks}
            {keyStatus && <div className="settings-status-error">{keyStatus}</div>}
          </section>
        </div>
      </div>
    </div>
    {showPool && (
      <KeyPoolModal onClose={() => setShowPool(false)} onSaved={handlePoolSaved} />
    )}
    </>
  );
}
