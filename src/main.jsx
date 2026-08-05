import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import DetachedApp from './DetachedApp.jsx';
import './styles/index.css';

// URLSearchParams drives the dispatch — main.jsx is the entry point for
// both the primary window (`index.html`) and the standalone "detached panel"
// window (`index.html?panel=result`). Keeping this here (instead of inside
// App.jsx) lets the detached window AVOID loading the heavy App bundle at
// all — it just renders the result panel and waits for the main window's
// backend to come up.
function isDetachedPanel() {
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get('panel') === 'result';
  } catch (_) {
    return false;
  }
}

function Bootstrap() {
  const [aguiPort, setAguiPort] = useState(null);
  const [waitSeconds, setWaitSeconds] = useState(0);

  useEffect(() => {
    if (!window.hermes) {
      setAguiPort(0);
      return;
    }

    let mounted = true;
    const refreshPort = () => {
      window.hermes.getAguiPort().then((port) => {
        if (mounted) setAguiPort(port || 0);
      }).catch(() => {
        if (mounted) setAguiPort(0);
      });
    };

    refreshPort();

    // The backend is started in parallel with the window; when it is ready
    // the main process notifies us so we can re-read the AG-UI port.
    const onReady = () => refreshPort();
    if (window.hermes.onAguiReady) {
      window.hermes.onAguiReady(onReady);
    }

    // Show a progressively more specific message so users don't think the
    // app is frozen during the several-second Hermes cold start.
    const timer = setInterval(() => {
      if (mounted) setWaitSeconds((s) => s + 1);
    }, 1000);

    return () => {
      mounted = false;
      clearInterval(timer);
      if (window.hermes.offAguiReady) {
        window.hermes.offAguiReady(onReady);
      }
    };
  }, []);

  if (aguiPort === null || aguiPort === 0) {
    const isLongWait = waitSeconds > 15;
    const stage = waitSeconds < 5 ? 'init'
      : waitSeconds < 15 ? 'runtime'
      : waitSeconds < 30 ? 'connecting'
      : 'check';
    const stageText = {
      init:      '正在初始化环境…',
      runtime:   '正在启动本地 runtime…',
      connecting:'正在连接后端服务…',
      check:     '启动时间较长，正在检查配置…',
    };
    return (
      <div className="app flex-center">
        <div className="welcome bootstrap-loading">
          <div className="bootstrap-spinner">
            <div className="spinner-ring" />
            <div className="spinner-logo">∞</div>
          </div>
          <h2>Abcyesno</h2>
          <p className="bootstrap-status">{stageText[stage]}</p>
          {isLongWait && (
            <p className="bootstrap-hint">
              已等待 <strong>{waitSeconds}</strong> 秒 · 首次启动或需要配置 API Key
            </p>
          )}
          <div className="bootstrap-progress">
            <div
              className="bootstrap-progress-bar"
              style={{ width: `${Math.min(100, (waitSeconds / 60) * 100)}%` }}
            />
          </div>
        </div>
      </div>
    );
  }

  return <App aguiPort={aguiPort} />;
}

// Standalone "detached panel" window — its own Bootstrap is in DetachedApp.jsx
// because it has entirely different loading-state messaging (the main window
// already owns the backend, so we just wait for it to come up).
const Root = isDetachedPanel() ? <DetachedApp /> : <Bootstrap />;
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {Root}
  </React.StrictMode>
);
