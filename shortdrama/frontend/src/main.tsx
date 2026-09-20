/* ==========================================================================
   src/main.tsx —— 入口
   --------------------------------------------------------------------------
   ★ 样式**不复制**：直接引用旧 `web/assets/css/*.css`。

   为什么这么做（这是一个刻意的架构决定，不是图省事）：
     本项目最贵的一类 bug 是「同一判据写两份」—— 改了一边忘了另一边。
     搬一份 app.css（61 KB）进 `frontend/` 就等于制造两份真相源，
     然后两边界面会以"看起来很细微"的方式开始分叉。
     ⇒ 过渡期两边共用一份 CSS：改一次，React 版和旧 web 版**同时**生效。
     ⇒ 等 `web/` 真要退役时把它们移进 `frontend/src/styles/` —— 那时 import
       会立刻报错，属于**可见的失败**，不会静默失效。
   ========================================================================== */

import { createRoot } from 'react-dom/client';
import '../../web/assets/css/tokens.css';
import '../../web/assets/css/app.css';
import { App } from './App';

const host = document.getElementById('app');
if (!host) throw new Error('找不到挂载点 #app（index.html 被改过？）');

createRoot(host).render(<App />);
