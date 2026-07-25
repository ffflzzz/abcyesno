import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import './styles/index.css';

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

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Bootstrap />
  </React.StrictMode>
);
