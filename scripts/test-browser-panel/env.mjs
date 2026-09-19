// 无浏览器宿主环境：让 BrowserPanel 的真实源码能在 Node 里被驱动、被断言。
// 只实现组件真正碰到的宿主 API（window / sessionStorage / <webview> 节点）。
// 假 webview 是单例 —— 组件的 ref 回调每次渲染都会重挂监听，单例才能让
// addEventListener/removeEventListener 的增删在同一对象上生效。

export const env = {
  // 假 webview 当前的"真实地址"（getURL 返回值）
  pageUrl: "about:blank",
  // loadURL 调用记录
  loaded: [],
  // marker 空白页地址（由用例注入）
  marker: "",
};

let node = null;

export function webviewNode() {
  if (node) return node;
  node = {
    src: "",
    _listeners: {},
    isReady: () => true,
    canGoBack: () => false,
    canGoForward: () => false,
    getURL: () => env.pageUrl,
    loadURL: (u) => {
      env.loaded.push(u);
      env.pageUrl = u;
      return Promise.resolve();
    },
    reload: () => {},
    goBack: () => {},
    goForward: () => {},
    getWebContentsId: () => 1,
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = this._listeners[type] || [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    /** 用例触发 Electron webview 的原生事件（did-navigate 等） */
    fire(type) {
      for (const fn of (this._listeners[type] || []).slice()) fn({ type });
    },
  };
  return node;
}

/** 安装全局宿主环境（幂等）。返回 hermes 桥存根，便于用例改行为。 */
export function installHost({ marker = "", cdpUrl = "ws://127.0.0.1:9222" } = {}) {
  env.marker = marker;
  const store = new Map();
  const hermes = {
    browserInfo: { marker, cdpUrl },
    reportBrowserWebview() {},
    clearBrowserWebview() {},
    openExternal() {},
    getBrowserInfo: async () => ({ ...hermes.browserInfo }),
  };
  globalThis.window = {
    innerWidth: 1400,
    hermes,
    addEventListener() {},
    removeEventListener() {},
    sessionStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
    },
  };
  return hermes;
}

export function resetEnv() {
  env.pageUrl = env.marker;
  env.loaded = [];
}

/** 让挂起的 promise / microtask / 一次宏任务全部落地 */
export const settle = (ms = 20) => new Promise((r) => setTimeout(r, ms));
