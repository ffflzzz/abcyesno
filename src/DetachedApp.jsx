import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ResultPanel from "./components/ResultPanel.jsx";
import { ErrorBoundary } from "./ErrorBoundary.jsx";
import { initContract, listManifests } from "./contract/registry.js";
import { subscribeContractEvents } from "./contract/eventBus.js";
import bachAvatar from "./assets/bach-avatar.png";

// ──────────────────────────────────────────────────────────────────────────
// DetachedApp — standalone window entry point for the detached result panel:
//
//  mode="result" (`index.html?panel=result`)
//    Renders ONLY the ResultPanel (no Sidebar / ChatLayout). Shares the same
//    backend as the main window; just waits for it to come up.
//
// (The "studio" standalone-window mode was removed — 漫剧go now opens as a
// normal in-app tab. This component now only serves the result panel.)
//
// Re-uses window.hermes preloaded by electron/preload.js and polls the main
// window's backend (the main window already started Hermes).
// ──────────────────────────────────────────────────────────────────────────

export default function DetachedApp({ mode = "result", workflowId = "" }) {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const initialWorkflowId = workflowId || params.get("workflow") || params.get("workflowId") || "";
  const initialTab = params.get("tab") || "overview";
  const initialSessionId = params.get("sessionId") || "";
  const initialCollapsed = params.get("collapsed") === "true";

  const [aguiPort, setAguiPort] = useState(0);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [backendStatus, setBackendStatus] = useState({ hermesReady: false, gatewayConnected: false });
  const [manifests, setManifests] = useState(() => listManifests());

  // Wait for the main window's backend to come online. Poll AG-UI port + status.
  useEffect(() => {
    if (!window.hermes) return;
    let mounted = true;
    const refresh = async () => {
      try {
        const port = await window.hermes.getAguiPort();
        if (!mounted) return;
        setAguiPort(port || 0);
      } catch (_) {
        if (mounted) setAguiPort(0);
      }
    };
    refresh();
    if (window.hermes.onAguiReady) {
      const handler = () => refresh();
      window.hermes.onAguiReady(handler);
      return () => {
        mounted = false;
        if (window.hermes.offAguiReady) window.hermes.offAguiReady(handler);
      };
    }
    const timer = setInterval(() => mounted && setWaitSeconds((s) => s + 1), 1000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!window.hermes) return;
    let mounted = true;
    const onStatus = (s) => mounted && setBackendStatus((p) => ({
      hermesReady: p.hermesReady || !!s.connected,
      gatewayConnected: p.gatewayConnected || !!s.connected,
    }));
    window.hermes.on("gateway-status", onStatus);
    window.hermes.getStatus?.().then((s) => {
      if (!mounted) return;
      setBackendStatus((p) => ({
        hermesReady: p.hermesReady || !!s?.hermesReady,
        gatewayConnected: p.gatewayConnected || !!s?.gatewayConnected,
      }));
    });
    return () => {
      mounted = false;
      window.hermes.off("gateway-status", onStatus);
    };
  }, []);

  // Contract layer pulls manifests from the AG-UI bridge once it's up.
  useEffect(() => {
    if (!aguiPort) return;
    initContract(aguiPort).then(setManifests);
  }, [aguiPort]);

  // Loading screen while the main window's backend is booting.
  if (!aguiPort) {
    return (
      <div className="app flex-center">
        <div className="welcome bootstrap-loading">
          <div className="bootstrap-spinner">
            <div className="spinner-ring" />
            <img className="spinner-logo spinner-logo-img" src={bachAvatar} alt="" draggable="false" />
          </div>
          <h2>结果面板</h2>
          <p className="bootstrap-status">
            {waitSeconds < 5 ? "等待主窗口启动后端…" : waitSeconds < 20 ? "正在连接到本地后端…" : "等待中 — 请确认主窗口已打开。"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="app detached-panel-app">
      <ErrorBoundary>
        <div className="result-panel-wrapper detached-panel-wrapper">
          <ResultPanel
            sessionId={initialSessionId}
            aguiPort={aguiPort}
            selectedWorkflowId={initialWorkflowId}
            manifests={manifests}
            onSend={async () => {}}
            onStop={async () => {}}
            onWorkflowRun={() => Promise.resolve(null)}
            backendStatus={backendStatus}
            onSelectWorkflow={() => {}}
            externalPreviewUrl={null}
            onClearExternalPreview={() => {}}
            collapsed={initialCollapsed}
            onToggleCollapse={() => {}}
            // Hide the detach button itself — we're already detached.
            detachHidden
            style={{ width: "100%", minWidth: 0, flexShrink: 0 }}
          />
        </div>
      </ErrorBoundary>
    </div>
  );
}
