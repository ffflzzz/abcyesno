import React, { useCallback, useEffect, useState } from 'react';
import bachAvatar from '../assets/bach-avatar.png';

// 更新提醒卡：主进程在「新版下载安装完成」时开一个无边框置顶小窗，
// 加载 dist/index.html?panel=update 渲染这一张卡。窗口本身无状态，
// from / to 两个版本号全部由 URL 参数带进来；三个按钮都走 updater-popup-action。
function readParam(name, fallback) {
  try {
    return new URLSearchParams(window.location.search).get(name) || fallback;
  } catch (_) {
    return fallback;
  }
}

export default function UpdatePopup() {
  const from = readParam('from', '');
  const to = readParam('to', '');
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
      if (e.key === 'Enter') act('install');
      else if (e.key === 'Escape') act('later');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [act]);

  return (
    <div className="up-card">
      <div className="up-head">
        <img className="up-logo" src={bachAvatar} alt="" draggable="false" />
        <span className="up-app">Abcyesno 更新</span>
        <button className="up-close" onClick={() => act('later')} title="稍后再说">×</button>
      </div>

      <div className="up-body">
        <div className="up-title">下载完成</div>
        <div className="up-versions">
          {from ? <span className="up-chip">v{from}</span> : null}
          <span className="up-arrow">→</span>
          <span className="up-chip is-new">v{to || '?'}</span>
        </div>
        <p className="up-hint">
          静默安装，不会再弹安装向导；装完自动重新打开。正在跑的任务会中断。
        </p>
      </div>

      <div className="up-foot">
        <button className="up-details" onClick={() => act('details')}>查看详情</button>
        <button className="up-install" onClick={() => act('install')} disabled={busy}>
          {busy ? '正在安装…' : '立即安装并重启'}
        </button>
      </div>
    </div>
  );
}
