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

const host = document.getElementById('app');
if (!host) throw new Error('找不到挂载点 #app（index.html 被改过？）');

createRoot(host).render(<Studio />);
