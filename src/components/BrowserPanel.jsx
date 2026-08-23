import React, { useEffect, useRef, useState, useCallback } from "react";

// BrowserPanel — right-side dock holding Electron's built-in Chromium as a
// <webview> (spec §5.5, route B). The agent drives this same page over CDP via
// the pw_browser_* tools, so the user watches the agent operate the browser
// live, inside the app. The user can ALSO manually browse by typing a URL in
// the address bar. Width is user-resizable via a drag handle on the left edge.
//
// `initialUrl` (optional): when provided, the panel loads this URL immediately
// as a STATIC browser tab (e.g. Excalidraw opened from the Launcher). In this
// mode the webview uses an isolated partition (NOT pw-browser, which the agent
// drives) and the "return to blank / hand back to agent" button is hidden
// because there is no agent controlling this tab.
// `fullscreen` (optional): render edge-to-edge (no left resize handle, width
// 100%) so it can fill a browser-type tab's content area.
export default function BrowserPanel({ progress = [], initialUrl = "", fullscreen = false } = {}) {
  const [marker, setMarker] = useState("");
  const [cdpAvailable, setCdpAvailable] = useState(true);
  const [ready, setReady] = useState(false);
  const [navigated, setNavigated] = useState(false);
  const [url, setUrl] = useState(initialUrl || "");
  // Static (initialUrl) tabs don't use the agent's pw-browser partition so the
  // user's manual browsing never collides with an in-flight agent session.
  const partition = initialUrl ? "static-browser" : "pw-browser";
  const [canGoBack, setCanGoBack] = useState(false);
  const [canGoForward, setCanGoForward] = useState(false);
  const [panelWidth, setPanelWidth] = useState(() => {
    // Persist width across open/close cycles via sessionStorage
    try {
      const saved = Number(sessionStorage.getItem("bp-width"));
      if (saved && saved >= 280) return saved;
    } catch (_) {}
    return 560; // default — wider than before (was 440)
  });
  const webviewRef = useRef(null);
  const webviewCleanupRef = useRef(null);
  const inputRef = useRef(null);
  const resizingRef = useRef(false);
  // Latest-value refs to dodge the useEffect + setInterval closure trap.
  // Without these the 1 Hz poller would see stale `url`/`navigated` from the
  // first render and never re-sync after Playwright navigates the webview.
  const urlRef = useRef("");
  const navigatedRef = useRef(false);
  useEffect(() => { urlRef.current = url; }, [url]);
  useEffect(() => { navigatedRef.current = navigated; }, [navigated]);

  useEffect(() => {
    let alive = true;
    if (window.hermes && window.hermes.getBrowserInfo) {
      window.hermes
        .getBrowserInfo()
        .then((info) => {
          if (!alive) return;
          if (info && info.marker) setMarker(info.marker);
          // cdpUrl is empty when remote-debugging-port isn't exposed; flag it
          // so the panel can show a "Playwright not connected" hint.
          setCdpAvailable(!!(info && info.cdpUrl));
        })
        .catch(() => {});
    }
    return () => {
      alive = false;
    };
  }, []);

  // --- Resize by dragging the left-edge handle ---
  const handleResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    resizingRef.current = true;
    const startX = e.clientX;
    const startW = panelWidth;

    const onMove = (ev) => {
      if (!resizingRef.current) return;
      const delta = startX - ev.clientX; // drag left → wider
      const next = Math.max(280, Math.min(startW + delta, window.innerWidth * 0.75));
      setPanelWidth(next);
    };

    const onUp = () => {
      resizingRef.current = false;
      try { sessionStorage.setItem("bp-width", String(panelWidth)); } catch (_) {}
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [panelWidth]);

  // --- Navigation helpers ---
  const syncNavState = () => {
    const wv = webviewRef.current;
    // Electron <webview> 的 canGoBack/canGoForward 必须在 dom-ready 之后才能调。
    // did-start-loading 等事件早于 dom-ready 触发，未 ready 时调用会抛
    // "The WebView must be attached to the DOM and the dom-ready event emitted"。
    if (!wv || !ready) return;
    setCanGoBack(!!wv.canGoBack && wv.canGoBack());
    setCanGoForward(!!wv.canGoForward && wv.canGoForward());
  };

  // Electron <webview> events (dom-ready, did-navigate, destroyed, etc.) are
  // NOT standard DOM events and will not be bound by React's prop system. We
  // must attach them directly via addEventListener on the webview node. Using a
  // ref callback guarantees binding/unbinding every time the <webview> node is
  // mounted or unmounted (important because the node is conditionally rendered
  // while marker is fetched asynchronously).
  const bindWebview = useCallback((wv) => {
    if (webviewCleanupRef.current) {
      webviewCleanupRef.current();
      webviewCleanupRef.current = null;
    }
    if (!wv) {
      webviewRef.current = null;
      return;
    }
    webviewRef.current = wv;
    const onDomReady = () => {
      setReady(true);
      syncNavState();
      try {
        if (typeof wv.getWebContentsId === 'function') {
          const id = wv.getWebContentsId();
          window.hermes && window.hermes.reportBrowserWebview && window.hermes.reportBrowserWebview(id);
        }
      } catch (_) {}
    };
    const onDestroyed = () => {
      try {
        window.hermes && window.hermes.clearBrowserWebview && window.hermes.clearBrowserWebview();
      } catch (_) {}
      setReady(false);
    };
    const onEvent = (e) => {
      if (e.type === "did-start-loading" || e.type === "did-navigate") {
        setNavigated(true);
      }
      if (e.type === "did-navigate" || e.type === "did-navigate-in-page") {
        try {
          setUrl(wv.getURL ? wv.getURL() : wv.src || "");
        } catch (_) {}
      }
      syncNavState();
    };
    // Electron <webview> silently drops target=_blank / window.open unless a
    // handler is attached. Forward such links to the system browser so embedded
    // pages (e.g. the paper dashboard's "下载 PDF") actually respond instead of
    // no-op. The main process also installs a setWindowOpenHandler (authoritative,
    // routes the dashboard PDF to a real disk download); when that fires it
    // returns deny and this renderer-side event never fires, so the two never
    // double-handle the same click.
    const onNewWindow = (e) => {
      const url = e && e.url;
      if (!url || !/^https?:/i.test(url)) return;
      try { e.preventDefault && e.preventDefault(); } catch (_) {}
      try {
        window.hermes && window.hermes.openExternal && window.hermes.openExternal(url);
      } catch (_) {}
    };
    wv.addEventListener("dom-ready", onDomReady);
    wv.addEventListener("destroyed", onDestroyed);
    wv.addEventListener("did-start-loading", onEvent);
    wv.addEventListener("did-navigate", onEvent);
    wv.addEventListener("did-navigate-in-page", onEvent);
    wv.addEventListener("new-window", onNewWindow);
    // If the webview is already ready by the time the ref callback runs,
    // manually fire so we don't miss the registration.
    try {
      if (wv.isReady && wv.isReady()) onDomReady();
    } catch (_) {}
    webviewCleanupRef.current = () => {
      wv.removeEventListener("dom-ready", onDomReady);
      wv.removeEventListener("destroyed", onDestroyed);
      wv.removeEventListener("did-start-loading", onEvent);
      wv.removeEventListener("did-navigate", onEvent);
      wv.removeEventListener("did-navigate-in-page", onEvent);
      wv.removeEventListener("new-window", onNewWindow);
    };
  }, []);

  // Native-driver navigation does NOT fire did-navigate on this <webview>
  // element, so the URL bar would stay stuck on the marker. Poll getURL() at
  // 1 Hz using latest-value refs to keep the bar + navigated state in sync.
  useEffect(() => {
    if (!marker) return undefined;
    const id = setInterval(() => {
      const wv = webviewRef.current;
      if (!wv) return;
      let current = "";
      try {
        current = wv.getURL ? wv.getURL() : (wv.src || "");
      } catch (_) {
        return;
      }
      if (current && current !== urlRef.current) setUrl(current);
      const onMarker = current === marker;
      if (onMarker && navigatedRef.current) setNavigated(false);
      if (!onMarker && !navigatedRef.current) setNavigated(true);
      syncNavState();
    }, 1000);
    return () => clearInterval(id);
  }, [marker]);

  const normalize = (raw) => {
    const s = (raw || "").trim();
    if (!s) return "";
    if (/^https?:\/\//i.test(s) || s.startsWith("data:") || s.startsWith("file:")) return s;
    if (s.includes(".") && !s.includes(" ")) return `https://${s}`;
    return `https://${s}`;
  };

  const go = () => {
    const wv = webviewRef.current;
    const target = normalize(url);
    if (!wv || !target) return;
    try {
      wv.loadURL(target);
      setNavigated(true);
    } catch (_) {}
  };

  const onKey = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      go();
    }
  };

  const resetToBlank = () => {
    const wv = webviewRef.current;
    if (wv && marker) {
      try {
        wv.loadURL(marker);
        setNavigated(false);
        setUrl(marker);
      } catch (_) {}
    }
  };

  // Hint overlay shows whenever the webview is sitting on the marker page.
  // The marker page is intentionally transparent/empty; this overlay is the
  // single source of truth for the idle UI, preventing ghosting of duplicate
  // text. If the native driver isn't reachable, layer an extra warning on top.
  const showHint = ready && !navigated;
  const showCdpWarn = ready && !navigated && !cdpAvailable;

  return (
    <div className={`browser-panel${fullscreen ? " fullscreen" : ""}`} style={fullscreen ? undefined : { width: panelWidth }}>
      {/* Left-edge drag handle for resizing (hidden in fullscreen mode) */}
      {!fullscreen && (
        <div
          className="bp-resize-handle"
          onMouseDown={handleResizeMouseDown}
          title="拖拽调整宽度"
        />
      )}
      <div className="browser-toolbar">
        <button
          className={`bt-btn ${canGoBack ? "" : "disabled"}`}
          title="后退"
          onClick={() => webviewRef.current && webviewRef.current.goBack()}
          disabled={!canGoBack}
        >
          ◀
        </button>
        <button
          className={`bt-btn ${canGoForward ? "" : "disabled"}`}
          title="前进"
          onClick={() => webviewRef.current && webviewRef.current.goForward()}
          disabled={!canGoForward}
        >
          ▶
        </button>
        <button
          className="bt-btn"
          title="刷新"
          onClick={() => webviewRef.current && webviewRef.current.reload()}
        >
          ⟳
        </button>
        <input
          ref={inputRef}
          className="bt-address"
          placeholder="输入网址，回车浏览（如 example.com）"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={onKey}
        />
        <button className="bt-btn go" title="前往" onClick={go}>
          →
        </button>
        {!initialUrl && (
          <button className="bt-btn" title="回到空白页（交还 Agent 控制）" onClick={resetToBlank}>
            ⌂
          </button>
        )}
      </div>
      <div className="browser-panel-body">
        {marker || initialUrl ? (
          <>
            <webview
              ref={bindWebview}
              className="browser-webview"
              src={initialUrl || marker}
              partition={partition}
              webpreferences="contextIsolation=true"
            />
            {showHint && (
              <div className="bp-hint">
                <span className="bp-hint-icon">🌐</span>
                <span>内置浏览器已就绪</span>
                <span className="bp-hint-sub">
                  {initialUrl
                    ? "可在上方输入网址手动浏览"
                    : "可在上方输入网址手动浏览，或等 Agent 调用浏览器工具"}
                </span>
                {showCdpWarn && (
                  <div className="bp-cdp-warn">
                    ⚠ Agent 浏览器驱动未连通 — 请确认桌面已启动且未禁用浏览器面板。
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="bp-hint">
            <span>正在连接内置浏览器…</span>
          </div>
        )}
      </div>
      {progress.length > 0 && (
        <div className="bp-progress">
          <div className="bp-progress-head">
            <span className="bp-progress-title">浏览器活动</span>
            <span className="bp-progress-count">{progress.length}</span>
          </div>
          <div className="bp-progress-list">
            {progress.slice(-6).map((p, i) => (
              <div key={p.ts || i} className={`bp-progress-item ${p.level || "info"}`}>
                <span className="bp-progress-dot" />
                <span className="bp-progress-msg">{p.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
