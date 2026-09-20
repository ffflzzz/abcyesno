/* ==========================================================================
   core/router.js —— 哈希路由
   路由表与线上 URL 结构对齐，便于对照：
     /playlet/list                                 短剧工作台首页
     /playlet/review/:pid                          三步向导（剧本大纲 / 资产库 / 分集视频）
     /playlet/review/:pid/episode/:eid             分镜编辑器
   ========================================================================== */
(function (global) {
  'use strict';

  const routes = [];
  let current = null;
  let notFound = null;

  function add(pattern, handler) {
    // '/playlet/review/:pid' -> 正则 + 参数名
    const names = [];
    const re = new RegExp('^' + pattern.replace(/:[A-Za-z0-9_]+/g, function (m) {
      names.push(m.slice(1));
      return '([^/]+)';
    }).replace(/\//g, '\\/') + '$');
    routes.push({ re: re, names: names, handler: handler, pattern: pattern });
  }

  function parse(hash) {
    let h = String(hash || '').replace(/^#/, '');
    if (!h) h = '/playlet/list';
    const q = h.indexOf('?');
    const query = {};
    if (q >= 0) {
      h.slice(q + 1).split('&').forEach(function (kv) {
        const i = kv.indexOf('=');
        if (i > 0) query[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
      });
      h = h.slice(0, q);
    }
    return { path: h, query: query };
  }

  function match(path) {
    for (let i = 0; i < routes.length; i++) {
      const r = routes[i];
      const m = path.match(r.re);
      if (m) {
        const params = {};
        r.names.forEach(function (n, idx) { params[n] = m[idx + 1]; });
        return { route: r, params: params };
      }
    }
    return null;
  }

  /**
   * 跳转。**唯一入口** —— 坏路径在这里一次性拦住。
   *
   * ⛔ 为什么要有这个守卫（2026-09-17 实测）：
   *   JS 里 `'...' + null` 会得到**字面串** `'...null'`（不报错、不抛异常），
   *   于是 hash 变成 `#/playlet/review/null` → 后端稳定 404「项目不存在：null」，
   *   而界面把它显示成"后端未连通"，**排查方向被带偏**。
   *   实测证据：shim 日志每次导航都跟着两条 `GET /projects/null/progress → 404`。
   *
   * 在**唯一入口**拦一次，胜过在 10 个调用点各写一遍守卫（新调用点也不会漏）。
   * ⚠️ 代价：项目**真的**叫 `null` 时打不开 —— 这属于病态命名，改名即可
   *    （v5 的 pid 是 `village-bees` 这类英文 slug）。
   */
  function go(path, replace) {
    const bad = /(^|\/)(null|undefined)(\/|$|\?)/.test(String(path || ''));
    if (bad) {
      console.warn('[router] 拒绝跳转：路径里有 null/undefined 段 →', path);
      if (global.Store && global.Store.toast) {
        global.Store.toast('页面参数缺失，已取消跳转（刷新页面重试）');
      }
      return false;
    }
    if (replace) location.replace('#' + path);
    else location.hash = path;
    return true;
  }

  function resolve() {
    const parsed = parse(location.hash);
    const hit = match(parsed.path);
    const params = hit ? hit.params : {};

    // ★ 地址栏里**已经**是 `.../null`（上次会话留下的链接 / 书签）时，
    //   光靠 `go()` 的守卫没用（那不是一次导航）—— 直接回列表页并说明。
    //   否则每次刷新都稳定 404，用户以为后端挂了（实测就是这个现象）。
    const hasBogus = Object.keys(params).some(function (k) {
      return params[k] === 'null' || params[k] === 'undefined';
    });
    if (hit && hasBogus && parsed.path !== '/playlet/list') {
      console.warn('[router] 路径参数含 null/undefined，回列表页：', parsed.path);
      if (global.Store && global.Store.toast) {
        global.Store.toast('链接参数异常（含 null），已回到项目列表');
      }
      location.replace('#/playlet/list');
      return;                        // 由随后的 hashchange 再走一遍 resolve（收敛）
    }

    current = { path: parsed.path, query: parsed.query, params: params, pattern: hit ? hit.route.pattern : null };
    if (hit) hit.route.handler(parsed.params || {}, parsed.query, current);
    else if (notFound) notFound(parsed.path);
    else global.App.renderNotFound(parsed.path);
    // 通知视图切换（用于滚动复位等）
    document.dispatchEvent(new CustomEvent('route:change', { detail: current }));
  }

  function start() {
    window.addEventListener('hashchange', resolve);
    resolve();
  }

  global.Router = {
    add, go, start, resolve,
    setNotFound: function (fn) { notFound = fn; },
    get current() { return current; },
    /** 生成链接 */
    href: function (path) { return '#' + path; },
  };
})(window);
