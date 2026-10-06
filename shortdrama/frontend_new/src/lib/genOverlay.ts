/* ==========================================================================
   src/lib/genOverlay.ts —— 全屏「过场动画」的状态层
   --------------------------------------------------------------------------
   用途：**等待生成**期间盖住整页，不让用户看到"还没内容的工作台"。
   用户原话：「剧本没有生成出来之前，不允许进入工作台才对，不然用户还看不到内容，
   只看到空白页面」——所以两段生成（简介 / 剧本正文）期间都要有它。

   两条硬纪律（渲染层 `components/GenOverlay.tsx` 负责落实）：
     ① **不抢人工确认条的层级**：`.gen-overlay` 是 `z-index: 1200`，
        而 `.hitl-bar` 是 1400。逐步人工确认开着时链会停在"派发下一个角色之前"等人
        —— 那一刻调用方必须 `hide()`（判据用"确认条在不在场"），
        这里只保证"就算两者同时在场，人也能看见确认条"。
     ② **toast（z-index 2000）要盖在它上面**：等待期间的状态提示不能被吃掉。

   为什么做成外部 store（而不是 React state）：调用点在 `Wizard` / `Storyboard`
   这些页面里，而**渲染点必须挂在最外层**（不能被页面重渲染带走）——
   这正是旧版"常驻 UI 挂 body"那条教训的解法：把状态与渲染点分开。
   ========================================================================== */

import { useSyncExternalStore } from 'react';

export interface GenOverlayState {
  title: string;
  sub: string;
  /** 非空才渲染「停止」 */
  onStop: (() => void) | null;
}

let cur: GenOverlayState | null = null;
const listeners = new Set<() => void>();

function emit(): void { listeners.forEach((fn) => fn()); }

export const GenOverlay = {
  show(o: { title?: string; sub?: string; onStop?: () => void }): void {
    cur = {
      title: o.title || '正在生成…',
      sub: o.sub || '',
      onStop: o.onStop || null,
    };
    emit();
  },
  /** 只改文案（幂等；调用方 `onStop` 每次都传同一个函数，故不重建） */
  update(o: { sub?: string; title?: string }): void {
    if (!cur) return;
    cur = Object.assign({}, cur, o);
    emit();
  },
  hide(): void { cur = null; emit(); },
  visible(): boolean { return !!cur; },
  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
  getState(): GenOverlayState | null { return cur; },
};

export function useGenOverlay(): GenOverlayState | null {
  return useSyncExternalStore(GenOverlay.subscribe, GenOverlay.getState, GenOverlay.getState);
}
