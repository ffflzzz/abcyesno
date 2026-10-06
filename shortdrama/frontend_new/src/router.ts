/* ==========================================================================
   src/router.ts —— 哈希路由
   路由表与线上 URL 结构**保持一致**，便于两边对照（也和旧 web 版一致）：
     /playlet/list                                  短剧工作台首页
     /playlet/review/:pid                           三步向导（剧本大纲 / 资产库 / 分集视频）
     /playlet/review/:pid/episode/:eid              分镜编辑器
   ========================================================================== */

import { useSyncExternalStore } from 'react';
import type { RouteInfo } from './types';

interface RouteDef {
  pattern: string;
  re: RegExp;
  names: string[];
}

function compile(pattern: string): RouteDef {
  const names: string[] = [];
  const re = new RegExp(
    '^' + pattern.replace(/:([A-Za-z0-9_]+)/g, (_m, n: string) => {
      names.push(n);
      return '([^/]+)';
    }).replace(/\//g, '\\/') + '$',
  );
  return { pattern, re, names };
}

const DEFS: RouteDef[] = [
  compile('/playlet/list'),
  compile('/playlet/review/:pid/episode/:eid'),
  compile('/playlet/review/:pid'),
  compile('/inspiration'),
  compile('/canvas'),
  compile('/chat'),
  compile('/visuals'),
];

const listeners = new Set<() => void>();
let current: RouteInfo = parse(location.hash);
let snapshot: RouteInfo = current;

function parse(hash: string): RouteInfo {
  let h = String(hash || '').replace(/^#/, '');
  if (!h) h = '/playlet/list';
  const q = h.indexOf('?');
  const query: Record<string, string> = {};
  if (q >= 0) {
    h.slice(q + 1).split('&').forEach((kv) => {
      const i = kv.indexOf('=');
      if (i > 0) {
        query[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
      }
    });
    h = h.slice(0, q);
  }
  for (const d of DEFS) {
    const m = h.match(d.re);
    if (!m) continue;
    const params: Record<string, string> = {};
    d.names.forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); });
    return { path: h, params, query, pattern: d.pattern };
  }
  return { path: h, params: {}, query, pattern: '' };
}

function onHashChange(): void {
  current = parse(location.hash);
  snapshot = current;                 // ★ 同一引用要稳定，否则 useSyncExternalStore 会死循环
  listeners.forEach((fn) => fn());
}

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', onHashChange);
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export const Router = {
  /**
   * 程序式跳转。`go('/playlet/review/' + id)`
   *
   * `replace=true` = **不新增历史记录**。步骤条切步骤用它 ——
   * 否则在向导里点两下步骤，浏览器的"后退"要点好几次才回到列表
   * （旧版 `Router.go(url, true)` 就是这个意思）。
   */
  go(path: string, replace = false): void {
    if (replace) location.replace('#' + path);
    else location.hash = '#' + path;
  },
  /** 当前路由（非 React 环境用，如测试里）。 */
  get current(): RouteInfo { return current; },
};

/** React 里读当前路由。 */
export function useRoute(): RouteInfo {
  return useSyncExternalStore(subscribe, () => snapshot, () => snapshot);
}

/** 给测试用：重置并派发一次 hashchange。 */
export function __setHashForTest(hash: string): void {
  location.hash = hash;
  onHashChange();
}
