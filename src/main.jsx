import React, { useEffect, useState, useCallback, useRef } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import DetachedApp from './DetachedApp.jsx';
import Onboarding, { isOnboardingDone } from './components/Onboarding.jsx';
import { TtsProvider } from './hooks/useTts.jsx';
import bachAvatar from './assets/bach-avatar.png';
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

// Boot mode router — main.jsx is the entry point for two surfaces:
//   1. main   : primary window (`index.html`)
//   2. result : detached result panel (`index.html?panel=result`)
// (The "studio" standalone-window mode was removed — 漫剧go now opens as a
// normal in-app tab, not a separate Electron window. Keeping dispatch here
// lets the detached result window avoid loading the heavy App bootstrap.)
function parseBootMode() {
  try {
    const p = new URLSearchParams(window.location.search);
    const panel = p.get('panel');
    if (panel === 'result') {
      return { mode: 'result', workflowId: '' };
    }
    if (panel === 'tab') {
      // Torn-off tab window. Phase 1: browser. Phase 2: studio (carries
      // workflowId so the workbench can load the right manifest).
      return {
        mode: 'tab',
        type: p.get('type') || 'browser',
        browserUrl: p.get('browserUrl') || '',
        workflowId: p.get('workflowId') || '',
        assistantId: p.get('assistantId') || '',
      };
    }
  } catch (_) {}
  return { mode: 'main', workflowId: '' };
}

function Bootstrap() {
  const [aguiPort, setAguiPort] = useState(null);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [needsOnboarding, setNeedsOnboarding] = useState(false);
  const [onboardingComplete, setOnboardingComplete] = useState(false);
  const onboardingDataRef = useRef(null);

  useEffect(() => {
    // One-time check: first launch?
    if (!isOnboardingDone()) {
      setNeedsOnboarding(true);
    }
  }, []);

  const handleOnboardingComplete = useCallback((data) => {
    onboardingDataRef.current = data;
    setOnboardingComplete(true);
  }, []);

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

  // ── Onboarding mode: first-launch questions while backend boots ──
  if (needsOnboarding && !onboardingComplete) {
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
        <div className="welcome bootstrap-loading onboarding-container">
          {/* Shared spinner + avatar */}
          <div className="bootstrap-spinner">
            <div className="spinner-ring" />
            <img className="spinner-logo spinner-logo-img" src={bachAvatar} alt="" draggable="false" />
          </div>

          <Onboarding
            ready={aguiPort !== null && aguiPort !== 0}
            onComplete={handleOnboardingComplete}
          />

          {isLongWait && (
            <p className="bootstrap-hint ob-init-hint">
              已等待 <strong>{waitSeconds}</strong> 秒 · {stageText[stage]}
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

  // ── Normal splash or App ──
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
            <img className="spinner-logo spinner-logo-img" src={bachAvatar} alt="" draggable="false" />
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

// Standalone windows — the detached result panel has its own Bootstrap in
// DetachedApp.jsx (it waits for the main window's backend to come up).
const boot = parseBootMode();
const Root =
  boot.mode === 'result'
    ? <DetachedApp mode="result" />
    : boot.mode === 'tab'
      ? <DetachedApp mode="tab" type={boot.type} browserUrl={boot.browserUrl} workflowId={boot.workflowId} assistantId={boot.assistantId} />
      : <Bootstrap />;
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <TtsProvider>
      {Root}
    </TtsProvider>
  </React.StrictMode>
);
