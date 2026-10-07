import React, { useCallback, useEffect, useState } from 'react';
import bachAvatar from '../assets/bach-avatar.png';

// 更新提醒卡：主进程在「查到新版本」时开一个无边框置顶小窗，
// 加载 dist/index.html?panel=update 渲染这一张卡。
// 卡是**一张、随状态换内容**：available（等你点下载）→ downloading（进度）
// → downloaded（等你点重启）→ error（可重试）。首帧从 URL 参数取，之后走
// updater-state 推送；窗口本身不存业务状态，主进程是唯一的真相源。
const KNOWN = ['available', 'downloading', 'downloaded', 'error'];

function readParam(name, fallback) {
  try {
    return new URLSearchParams(window.location.search).get(name) || fallback;
  } catch (_) {
    return fallback;
  }
}

function mb(bytes) {
  if (!bytes || bytes < 0) return '';
  return (bytes / 1024 / 1024).toFixed(1);
}

// 主进程 state 快照 → 卡片要显示的那几个字段
function viewOf(s) {
  return {
    status: s.status,
    to: (s.info && s.info.version) || '',
    percent: s.progress ? Math.min(100, Math.max(0, s.progress.percent || 0)) : 0,
    got: s.progress ? mb(s.progress.transferred) : '',
    all: s.progress ? mb(s.progress.total) : '',
    err: (s.error || '').slice(0, 56),
  };
}

// 每种状态一句话 + 主按钮的字与动作。装机这步必须点，绝不自动重启：
// 正在跑的创作链/微信桥不能被杀。
const STAGE = {
  available: {
    title: '发现新版本',
    hint: '点「下载并更新」才开始下载；下完还要你再点一下才会重启安装。',
    btn: '下载并更新',
    act: 'download',
  },
  downloading: {
    title: '正在下载',
    hint: '下载不打扰你正常用软件，下完这张卡会自己变成「已就绪」。',
    btn: 'downloading',
    act: null,
  },
  downloaded: {
    title: '下载完成',
    hint: '静默安装，不会再弹安装向导；装完自动重新打开。正在跑的任务会中断。',
    btn: '立即安装并重启',
    act: 'install',
  },
  error: {
    title: '更新失败',
    hint: '网络或下载出错，点重试再来一次。你也可以先去发布页自己下。',
    btn: '重试',
    act: 'retry',
  },
};

export default function UpdatePopup() {
  const from = readParam('from', '');
  const [view, setView] = useState(() => ({
    status: KNOWN.includes(readParam('status', '')) ? readParam('status', '') : 'available',
    to: readParam('to', ''),
    percent: 0,
    got: '',
    all: '',
    err: '',
  }));
  const [busy, setBusy] = useState(false);

  // 独立小窗是另一个 document，不跟主窗口同步主题会一张深卡浮在浅色系统上。
  useEffect(() => {
    let theme = 'dark';
    try {
      theme = localStorage.getItem('abcyesno:theme') || 'dark';
    } catch (_) {}
    const resolved = theme === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : theme;
    document.documentElement.setAttribute('data-theme', resolved);
  }, []);

  // 真状态来自「更新检查」那次查询，URL 参数只是首帧。
  useEffect(() => {
    const h = window.hermes;
    if (!h || !h.getUpdaterState) return undefined;
    let alive = true;
    h.getUpdaterState().then((s) => {
      if (alive && s && KNOWN.includes(s.status)) setView(viewOf(s));
    }).catch(() => {});
    const onState = (s) => { if (s && KNOWN.includes(s.status)) setView(viewOf(s)); };
    h.onUpdaterState(onState);
    return () => {
      alive = false;
      h.offUpdaterState(onState);
    };
  }, []);

  const stage = STAGE[view.status] || STAGE.available;
  const isDownloading = view.status === 'downloading';

  const act = useCallback((action) => {
    const h = window.hermes;
    if (!h || !h.updaterPopupAction) return;
    if (action === 'install') setBusy(true);
    h.updaterPopupAction(action).catch(() => {
      if (action === 'install') setBusy(false);
    });
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Enter' && stage.act) act(stage.act);
      else if (e.key === 'Escape') act('later');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [act, stage.act]);

  const primary = isDownloading ? `正在下载… ${view.percent}%` : stage.btn;

  return (
    <div className="up-card">
      <div className="up-head">
        <img className="up-logo" src={bachAvatar} alt="" draggable="false" />
        <span className="up-app">Abcyesno 更新</span>
        <button className="up-close" onClick={() => act('later')} title="稍后再说">×</button>
      </div>

      <div className="up-body">
        <div className="up-title">{stage.title}</div>
        <div className="up-versions">
          {from ? <span className="up-chip">v{from}</span> : null}
          <span className="up-arrow">→</span>
          <span className="up-chip is-new">v{view.to || '?'}</span>
        </div>
        {isDownloading ? (
          <div className="up-progress">
            <div className="up-progress-fill" style={{ width: `${view.percent}%` }} />
          </div>
        ) : null}
        <p className="up-hint">
          {view.status === 'error' && view.err ? `${view.err} · ` : ''}
          {stage.hint}
        </p>
        {isDownloading && view.all ? (
          <p className="up-size">{view.got} MB / {view.all} MB</p>
        ) : null}
      </div>

      <div className="up-foot">
        <button className="up-details" onClick={() => act('details')}>查看详情</button>
        <button
          className="up-install"
          onClick={() => stage.act && act(stage.act)}
          disabled={!stage.act || busy}
        >
          {busy ? '正在安装…' : primary}
        </button>
      </div>
    </div>
  );
}
