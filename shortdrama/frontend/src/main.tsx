/* ==========================================================================
   src/main.tsx —— 入口
   --------------------------------------------------------------------------
   样式在 `src/styles/`（`tokens.css` + `app.css`）。

   这两份原本**不复制**、直接引用旧 `web/assets/css/` —— 那是退役期的刻意安排：
   搬一份 app.css 就等于制造两份真相源，两边界面会以"看起来很细微"的方式开始分叉。
   2026-09-30 旧 web 退役，共用期结束，按原计划把它们移进来。
   ========================================================================== */

import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/app.css';
import { App } from './App';

const host = document.getElementById('app');
if (!host) throw new Error('找不到挂载点 #app（index.html 被改过？）');

createRoot(host).render(<App />);
