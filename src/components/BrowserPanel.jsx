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
  // 面板宽度夹紧（2026-08-27「内置浏览器显示不全——窗口裁切」）：
  // 面板绝对定位于内容区右侧，宽度超过可用空间时右半被窗口边界直接裁掉。
  // 规则：聊天列至少保留 380px + 侧栏/边距 80px，其余才允许给面板。
  const clampWidth = (w) => {
    const maxW = Math.max(320, window.innerWidth - 380 - 80);
    return Math.max(280, Math.min(w, maxW));
  };
  const [panelWidth, setPanelWidth] = useState(() => {
    // Persist width across open/close cycles via sessionStorage
    try {
      const saved = Number(sessionStorage.getItem("bp-width"));
      if (saved && saved >= 280) return clampWidth(saved);
    } catch (_) {}
    return clampWidth(560); // default — wider than before (was 440)
  });
  const panelWidthRef = useRef(panelWidth);
  useEffect(() => { panelWidthRef.current = panelWidth; }, [panelWidth]);
  // 窗口变小时同步收缩面板，避免被窗口边界裁切
  useEffect(() => {
    const onWinResize = () => setPanelWidth((w) => clampWidth(w));
    window.addEventListener("resize", onWinResize);
    return () => window.removeEventListener("resize", onWinResize);
  }, []);
  const webviewRef = useRef(null);
  const webviewCleanupRef = useRef(null);
  const inputRef = useRef(null);
  const resizingRef = useRef(false);
  // ── 地址栏双态（2026-09-19「地址栏无法输入、被秒覆盖」修复）──────────────
  // url   = webview 的真实地址，唯一真源，只由 did-navigate 事件与 1 Hz 轮询写入。
  // draft = 用户正在编辑的文本；null 表示"未编辑"，地址栏直接展示 url。
  // 旧实现把用户输入直接写进 url，而轮询用 `current !== urlRef.current` 判
  // "页面跳转了"——刚敲一个字符就满足该条件，1 秒内被 getURL() 回灌覆盖；
  // 同时焦点保护读的是闭包里的 addressFocused，而该 effect 依赖只有 [marker]，
  // 值永远定格在首帧 false，保护从未生效（两层缺陷叠乘 → 完全无法输入）。
  // 现在输入只改 draft，url 保持纯净；是否编辑中改用 ref 实时读取，不落进闭包。
  const [draft, setDraft] = useState(null);
  // Latest-value refs to dodge the useEffect + setInterval closure trap.
  // Without these the 1 Hz poller would see stale `url`/`navigated` from the
  // first render and never re-sync after Playwright navigates the webview.
  const urlRef = useRef("");
  const navigatedRef = useRef(false);
  const draftRef = useRef(null);
  const editingRef = useRef(false);
  // 用户刚回车提交的原始文本。有了它才能区分两种"页面地址变了"：
  //  · 自己提交的那次跳转 → 落地后可以把地址栏换成权威地址（哪怕焦点还在输入框）
  //  · Agent / 链接引起的外部跳转 → 用户没在编辑时才可以动地址栏
  const submittedRef = useRef(null);
  useEffect(() => { urlRef.current = url; }, [url]);
  useEffect(() => { navigatedRef.current = navigated; }, [navigated]);
  useEffect(() => { draftRef.current = draft; }, [draft]);

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
      setPanelWidth(clampWidth(startW + delta));
    };

    const onUp = () => {
      resizingRef.current = false;
      // 持久化最终宽度（旧实现读闭包里的 panelWidth，存的是拖动前的旧值）
      try { sessionStorage.setItem("bp-width", String(panelWidthRef.current)); } catch (_) {}
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  // 地址权威化：一次真实跳转落地后，把地址栏从草稿换回 getURL() 的权威地址。
  // 唯一例外 —— 用户正在输入且这份草稿不是他刚提交的那次（外部跳转不许抢字）。
  const reconcileDraft = () => {
    if (draftRef.current === null) {
      submittedRef.current = null;
      return;
    }
    const ownSubmit =
      submittedRef.current !== null && draftRef.current === submittedRef.current;
    if (ownSubmit || !editingRef.current) {
      submittedRef.current = null;
      setDraft(null);
    }
  };

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
        // 真实跳转落地后让地址栏回到权威地址（用户回车或 Agent 驱动都走这里）
        reconcileDraft();
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
      // 只以 url（页面真实地址）为比较基准 —— 用户的输入存在 draft 里，
      // 不再污染 url，因此"正在输入"不会被误判成"页面跳转了"。
      if (current && current !== urlRef.current) {
        setUrl(current);
        // 地址确实变了（Agent 驱动 / 链接跳转）→ 地址栏回到权威地址
        reconcileDraft();
      }
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
    // 优先用草稿；草稿为空串（在空白页直接输入前）时回落 url
    const raw = draftRef.current !== null && draftRef.current !== "" ? draftRef.current : url;
    const target = normalize(raw);
    if (!wv || !target) return;
    try {
      wv.loadURL(target);
      setNavigated(true);
      // 记下这次提交：新地址落地前地址栏继续显示用户输入（否则会闪回旧地址），
      // 落地后由 reconcileDraft() 换成权威地址。
      submittedRef.current = raw;
    } catch (_) {}
  };

  const onKey = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      go();
    } else if (e.key === "Escape") {
      // 放弃编辑：回到真实地址
      e.preventDefault();
      editingRef.current = false;
      setDraft(null);
      try { e.target.blur(); } catch (_) {}
    }
  };

  const onAddressFocus = (e) => {
    editingRef.current = true;
    if (draftRef.current !== null) {
      // 上次失焦时保留的未提交草稿 → 全选让用户可整体替换，但不要覆盖
      try { e.target.select(); } catch (_) {}
      return;
    }
    // 首次聚焦：把当前地址灌入草稿并全选，敲字即整体替换（浏览器惯例）
    const seed = url === marker ? "" : url;
    setDraft(seed);
    if (seed) {
      try { e.target.select(); } catch (_) {}
    }
  };

  const onAddressBlur = () => {
    // 只解除"编辑中"标记，草稿保留（用户可能还想点"前往"）。
    // 之后任何真实跳转都会把草稿清掉，地址栏不会长期挂着过期文本。
    editingRef.current = false;
  };

  const resetToBlank = () => {
    const wv = webviewRef.current;
    if (wv && marker) {
      try {
        wv.loadURL(marker);
        setNavigated(false);
        setUrl(marker);
        setDraft(null);
        submittedRef.current = null;
      } catch (_) {}
    }
  };

  // 地址栏展示值：编辑中显示草稿，空闲显示真实地址。
  // marker 是内部空白页（一长串 data:），对用户无意义 → 显成空串露出占位提示。
  const addressValue = draft !== null ? draft : (url === marker ? "" : url);

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
          value={addressValue}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onFocus={onAddressFocus}
          onBlur={onAddressBlur}
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
              allowpopups
              /* 2026-08-27「网页显示不全」根因：guest 页面尺寸在创建瞬间被定格
                 （实测 innerWidth=1078=整窗宽度，面板只有 ~590px），之后元素
                 CSS 变化不再传导 → 右侧布局被裁、拖手柄"无效"。autosize 让
                 guest 跟随元素实际尺寸（min/max 给足余量）。 */
              autosize="on"
              minwidth="280"
              minheight="200"
              maxwidth="4000"
              maxheight="4000"
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
