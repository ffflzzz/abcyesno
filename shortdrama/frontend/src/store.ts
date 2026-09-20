/* ==========================================================================
   src/store.ts —— 应用状态（外部 store，供 useSyncExternalStore 消费）
   --------------------------------------------------------------------------
   为什么可以直接用旧的形状：旧 `core/store.js` **本来就已经是一个外部 store**
   （`emit()` / `subscribe(fn)`），所以 React 侧不需要 Redux / Zustand ——
   `useSyncExternalStore(subscribe, getSnapshot)` 就是为这种对象设计的。
   这是「迁移成本低于预期」的一个具体来源。
   --------------------------------------------------------------------------
   ⚠️ **派生数据不落 localStorage**（本项目的既有教训）：厂商列表 / 风格库 /
   项目列表都是**服务端决定的**，本地缓存一份会在后端变化后变成错的
   （实测：v5 新增第 4 个类型包后前端仍显示 3 个）。
   ========================================================================== */

import { useSyncExternalStore } from 'react';
import type { Episode, Project, StylePack, Vendors } from './types';
import { Api } from './api';

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'ok' | 'error';
}

export interface AppState {
  styles: StylePack[];
  vendors: Vendors | null;
  projects: Project[];
  /** 当前打开的项目（向导页 / 分镜页用） */
  project: Project | null;
  episode: Episode | null;
  /** 上一次水合的结果（界面可以如实播报失败原因） */
  hydrate: { hydrated: boolean; from?: string; error?: string } | null;
  toasts: Toast[];
}

let state: AppState = {
  styles: [],
  vendors: null,
  projects: [],
  project: null,
  episode: null,
  hydrate: null,
  toasts: [],
};

const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((fn) => { try { fn(); } catch { /* 单个订阅者出错不该拖垮其它 */ } });
}

function set(patch: Partial<AppState>): void {
  state = Object.assign({}, state, patch);
  emit();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/* ------------------------------------------------------------------ toast */

let toastSeq = 0;

/**
 * 提示条。**失败必须可见**（本项目铁律）：默认 3 秒自动消失，
 * 但 `kind='error'` 的留 6 秒 —— 出错时用户需要时间读完。
 */
function toast(text: string, kind: Toast['kind'] = 'info'): void {
  const id = ++toastSeq;
  set({ toasts: [...state.toasts, { id, text: String(text), kind }] });
  setTimeout(() => {
    set({ toasts: state.toasts.filter((t) => t.id !== id) });
  }, kind === 'error' ? 6000 : 3000);
}

/* -------------------------------------------------------------- 数据写入口 */

const byId = new Map<string, Project>();

export const Store = {
  subscribe,
  getState(): AppState { return state; },
  toast,

  allProjects(): Project[] { return state.projects; },
  getProject(pid: string): Project | null {
    return state.projects.find((p) => p.id === pid) || byId.get(pid) || null;
  },

  setStyles(list: StylePack[]): void { set({ styles: list || [] }); },
  setVendors(v: Vendors | null): void { set({ vendors: v || null }); },
  setHydrate(r: AppState['hydrate']): void { set({ hydrate: r }); },

  /**
   * 合并项目列表。`replace=true` 时以传入列表为**全集**
   * （服务端是权威，必须移除本地多出来的 —— 否则出厂种子项目会与真项目混在一起）。
   */
  upsertProjects(list: Project[], replace = false): void {
    const incoming = (list || []).filter((p) => p && p.id);
    incoming.forEach((p) => byId.set(p.id, p));
    if (replace) {
      set({ projects: incoming });
      return;
    }
    const merged = state.projects.slice();
    incoming.forEach((p) => {
      const i = merged.findIndex((x) => x.id === p.id);
      if (i >= 0) merged[i] = Object.assign({}, merged[i], p);
      else merged.push(p);
    });
    set({ projects: merged });
  },

  upsertProject(p: Project): void {
    if (!p || !p.id) return;
    byId.set(p.id, p);
    Store.upsertProjects([p], false);
    set({ project: p, episode: (p.episodes && p.episodes[0]) || state.episode });
  },

  upsertStoryboard(pid: string, eid: string, sb: unknown): void {
    const p = Store.getProject(pid);
    if (!p) {
      console.warn('[store] upsertStoryboard 找不到项目，分镜无处可挂：', pid);
      return;
    }
    const episodes = (p.episodes || []).map((ep) =>
      ep.id === eid ? Object.assign({}, ep, { storyboard: sb }) : ep);
    Store.upsertProject(Object.assign({}, p, { episodes }));
    set({ episode: episodes.find((e) => e.id === eid) || state.episode });
  },

  /** 本地乐观更新某一集（剧本正文 / 标题）。**不触发重新拉取** —— 保输入框不丢焦点。 */
  patchEpisode(pid: string, eid: string, patch: Partial<Episode>): void {
    const p = Store.getProject(pid);
    if (!p) return;
    const episodes = (p.episodes || []).map((ep) =>
      (ep.id === eid ? Object.assign({}, ep, patch) : ep));
    Store.upsertProject(Object.assign({}, p, { episodes }));
    if (state.episode && state.episode.id === eid) {
      set({ episode: Object.assign({}, state.episode, patch) });
    }
  },

  /** 本地乐观更新项目字段（简介 / 自动同步开关等）。 */
  patchProject(pid: string, patch: Partial<Project>): void {
    const p = Store.getProject(pid);
    if (!p) return;
    Store.upsertProject(Object.assign({}, p, patch));
  },

  setEpisode(e: Episode | null): void { set({ episode: e }); },

  /** 重命名（本地立即生效，服务端成功后不重新拉列表 —— 避免闪烁）。 */
  renameLocal(pid: string, name: string): void {
    const p = Store.getProject(pid);
    if (!p) return;
    Store.upsertProjects([Object.assign({}, p, { name })], false);
  },

  removeLocal(pid: string): void {
    set({ projects: state.projects.filter((p) => p.id !== pid) });
  },

  /** 离线模式（local 驱动）的初始数据：直接引用旧 `web/assets/js/data/seed.js`。 */
  loadSeed(): void {
    const seed = (window as unknown as { PAVO_SEED?: { styles?: StylePack[]; projects?: Project[] } }).PAVO_SEED;
    if (!seed) {
      console.warn('[store] 未找到 PAVO_SEED：离线数据不可用');
      return;
    }
    set({ styles: seed.styles || [], projects: seed.projects || [] });
  },
};

/** React 里读整个 state。 */
export function useStore(): AppState {
  return useSyncExternalStore(Store.subscribe, Store.getState, Store.getState);
}

/** 探活并把结果写进 state（设置页/顶栏显示用）。 */
export async function probeBackend(): Promise<void> {
  const r = await Api.ping();
  Store.setHydrate({ hydrated: r.ok, from: 'ping', error: r.error });
}

/** 一集的规模统计（移植自 `core/store.js` 的 `episodeStats`）。 */
export function episodeStats(ep: Episode | null | undefined): {
  segments: number; shots: number; durationMs: number; keyframes: number; videos: number;
} {
  const segs = (ep?.storyboard?.segments) || [];
  const shotCount = segs.reduce(
    (a, s) => a + (s.scenes || []).reduce((b, c) => b + ((c.shots || []).length), 0), 0,
  );
  const totalMs = segs.reduce((a, s) => a + (s.duration_ms || 0), 0);
  return {
    segments: segs.length,
    shots: shotCount,
    durationMs: totalMs,
    keyframes: segs.filter((s) => s.keyframe).length,
    videos: segs.filter((s) => s.video).length,
  };
}

/** ms → mm:ss（移植自 `core/dom.js` 的 `fmtDuration`）。 */
export function fmtDuration(ms: number | undefined): string {
  const total = Math.round((ms || 0) / 1000);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
}
