/* ==========================================================================
   src/lib/canvasApp.ts —— 画布应用（Infinite Atelier）的地址
   --------------------------------------------------------------------------
   画布是**另一个应用**，不在本 SPA 的路由里，所以跳转只能是整页导航，
   不能用 Router.go。地址收在这一个文件里，别的地方不许再拼一遍 ——
   ③「同源挂载」做完后，只需把 DEFAULT_BASE 从开发端口改成 `/atelier`，
   页面侧一行都不用动。

   `?from=<接口地址>`：画布应用认这个参数，会拉取、导入并直接跳进那张画布，
   所以不需要"导出文件 → 手动导入"那一步。
   ========================================================================== */

/** 同源挂在后端下（`v5/server.py` 的 `/atelier` 挂载）—— 不用再单开 3000 端口。
 *  开发期想跑画布自己的 vite dev，在控制台设
 *  `localStorage.setItem('sd.canvasAppBase.v1','http://localhost:3000')` 即可。 */
const DEFAULT_BASE = '/atelier';

const LS_KEY = 'sd.canvasAppBase.v1';

/** 允许在浏览器控制台临时改口（`localStorage.setItem('sd.canvasAppBase.v1','/atelier')`），
 *  用于验证 ③ 之后的形态而不必先改代码。 */
export function canvasAppBase(): string {
  let v = '';
  try { v = localStorage.getItem(LS_KEY) || ''; } catch { v = ''; }
  return (v || DEFAULT_BASE).replace(/\/+$/, '');
}

/** 某一集的画布接口。给**绝对**地址：画布应用与后端不同源时，
 *  相对地址会被浏览器解析到画布自己那个源 ⇒ 每个节点都是破图（实测踩过）。 */
export function canvasApiUrl(pid: string, ep: number): string {
  return `${location.origin}/v1/pixa/short-drama/projects/${encodeURIComponent(pid)}/canvas?ep=${ep}`;
}

export function canvasOpenUrl(pid: string, ep: number): string {
  return `${canvasAppBase()}/canvas?from=${encodeURIComponent(canvasApiUrl(pid, ep))}`;
}

/**
 * 打开某一集的画布。**新标签被拦下时回落到当前标签内导航。**
 *
 * ★ 为什么必须有这条回落：本工作台在 Abcyesno 桌面应用里是嵌在 `<webview>` 里跑的，
 *   宿主对 guest 的 `window.open` 一律 `deny`（`electron/main.js` 的
 *   `setWindowOpenHandler` —— 它的处置是"转交系统浏览器"，成不成、以及人就此离开
 *   本应用，两条都不在这个页面能控制的范围内）。被拦时 `window.open` 返回 null。
 *   ⇒ 拿不到新标签就自己导航：**宁可换页，不要"点了没反应"**。
 *   实测（2026-10-04）桌面应用里点画布入口没任何反应，就是这个通道没通过。
 */
export function openCanvasApp(pid: string, ep: number): void {
  const url = canvasOpenUrl(pid, ep);
  let w: Window | null = null;
  try { w = window.open(url, '_blank', 'noopener'); } catch { w = null; }
  if (!w || w.closed) location.href = url;
}
