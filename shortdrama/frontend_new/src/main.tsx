/* ==========================================================================
   src/main.tsx —— 入口
   --------------------------------------------------------------------------
   工作室是**唯一一个界面**：数据层（`api.ts` / `types.ts` / `lib/*`）整份复用
   旧工作台，视图层换成三栏（`pages/Studio.tsx`）。
   旧工作台没被改也没被删 —— 它仍由后端挂在 `/`（`v5/server.py` 的 `--web-root`），
   所以这里不需要留一条 `#/legacy` 回去：两个前端各自一个挂载点，互不覆盖。
   ========================================================================== */

import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/studio.css';
import { Studio } from './pages/Studio';

/**
 * 画布那份 280px 的节点清单**默认收起**。
 *
 * 为什么值得动：中栏 925px 里它吃掉 280px（`CANVAS_SIDE_PANEL_DEFAULT_WIDTH`），
 * 画布只剩 645px —— 而这份清单列的就是画布上已经看得见的那些格子，纯重复。
 *
 * ⚠️ 键名与 atelier 内部一致（`use-canvas-side-panel-store.ts` 的 `OPEN_KEY`），
 *   且**只在没设过时写** —— 人手动展开过就尊重他的选择。
 * ⚠️ `localStorage` 是**整个 origin 共用**的，所以这条也会影响 `/` 那个旧工作台
 *   首次打开画布时的默认值。要收回去就删掉这两行。
 */
try {
  if (localStorage.getItem('canvas-side-panel-open') === null) {
    localStorage.setItem('canvas-side-panel-open', '0');
  }
} catch { /* 隐私模式拿不到 storage，画布多占 280px 而已 */ }

const host = document.getElementById('app');
if (!host) throw new Error('找不到挂载点 #app（index.html 被改过？）');

createRoot(host).render(<Studio />);
