/* ==========================================================================
   src/lib/studio.ts —— 三栏工作室的数据面 + 画布桥（宿主侧）
   --------------------------------------------------------------------------
   与 `api.ts` 的分工：`api.ts` 是**旧工作台**的适配层，逐条从 `web/api.js` 移植，
   动它等于同时改两个前端。工作室独有的三条新端点（导演信箱 / 画布 / 带来源的
   分镜写入）放在这里，**只长不插**。
   ========================================================================== */

import { http } from '../api';

const P = '/v1/pixa/short-drama';

/* ─────────────────────────────── 画布（实时视图的数据源） ─────────────────────────────── */

export type CanvasNode = {
  id: string;
  type: string;
  title: string;
  position: { x: number; y: number };
  width: number;
  height: number;
  metadata?: Record<string, unknown>;
};

export type CanvasDoc = {
  title?: string;
  nodes: CanvasNode[];
  connections: { id: string; fromNodeId: string; toNodeId: string }[];
  viewport?: { x: number; y: number; k: number };
  fingerprint?: string;
  warnings?: string[];
  stats?: Record<string, unknown>;
};

/** 拉本集画布。后端**每次请求都重算**（读 stills.json / video_jobs.json / assets.json），
 *  所以轮询它 = 轮询磁盘事实。`fingerprint` 变了才往画布推，避免每 3 秒重排一次。 */
export function fetchCanvas(pid: string, ep: number): Promise<CanvasDoc> {
  return http<CanvasDoc>('GET', `${P}/projects/${encodeURIComponent(pid)}/canvas?ep=${ep}`);
}

/* ─────────────────────────────── 导演信箱 ─────────────────────────────── */

export type InboxItem = {
  id: number;
  kind: 'message' | 'edit';
  ep: number;
  text: string;
  by?: string;
  at?: string;
  to?: string;
  clipped?: boolean;
  delivered_to?: string;
  delivered_at?: string;
  meta?: Record<string, unknown>;
};

export type InboxState = {
  messages: InboxItem[];
  pending: InboxItem[];
  edits: InboxItem[];
  stats: { messages: number; pending: number; edits_live: number; edits_total: number };
  hitl: {
    pending: boolean; stamp?: string; next_role?: string; prev_role?: string;
    redo_targets?: string[]; manual_steps?: boolean | null; stale_decision?: unknown;
  };
  manual_steps: boolean;
  targets: string[];
};

export function getInbox(pid: string, ep: number): Promise<InboxState> {
  return http<InboxState>('GET', `${P}/projects/${encodeURIComponent(pid)}/director/inbox?ep=${ep}`);
}

export function sendToDirector(pid: string, text: string, ep: number, to = ''): Promise<InboxItem> {
  return http<InboxItem>('POST', `${P}/projects/${encodeURIComponent(pid)}/director/message`,
    { text, ep, to });
}

/** 「让导演重做」——**只有人点它才回退**。编辑本身不触发重跑（整表重派实测 ≈95 分钟）。 */
export function askRedo(pid: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('POST',
    `${P}/projects/${encodeURIComponent(pid)}/director/redo`, body);
}

/* ─────────────────────────────── 分镜写入（带来源） ─────────────────────────────── */

/** `Api.updateSegment` 不带 `source` ⇒ 台账会记成"网页"改的。工作室必须标 `canvas`，
 *  因为导演读到的那条账要区分"人在画布上点了一格改的"和"在表里改的"。 */
export function editShot(pid: string, ep: number, shot: string,
                         patch: Record<string, unknown>): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('POST', `${P}/segments/${pid}-ep${ep}-${shot}`,
    { ...patch, source: 'canvas' });
}

/* ─────────────────────────────── 画布桥（宿主侧） ─────────────────────────────── */

export const CANVAS_SOURCE = 'pixa-canvas';
export const HOST_SOURCE = 'pixa-host';

export type CanvasEvent =
  | { kind: 'pixa:hello' | 'pixa:ready'; payload: Record<string, unknown> }
  | { kind: 'pixa:nodes'; payload: { count: number; fingerprint: string; shots: string[] } }
  | { kind: 'pixa:select'; payload: { shot: string } };

/** 把新的节点集推给画布 iframe。**保位合并发生在画布那一侧**（它才知道人拖过哪些）。 */
export function pushCanvas(frame: HTMLIFrameElement | null, doc: CanvasDoc) {
  const w = frame?.contentWindow;
  if (!w) return;
  w.postMessage({ source: HOST_SOURCE, kind: 'pixa:merge',
    payload: { nodes: doc.nodes, connections: doc.connections, title: doc.title } }, '*');
}

export function pingCanvas(frame: HTMLIFrameElement | null) {
  frame?.contentWindow?.postMessage({ source: HOST_SOURCE, kind: 'pixa:ping' }, '*');
}

/** 订阅画布发回来的消息。返回退订函数。 */
export function onCanvasEvent(fn: (ev: CanvasEvent) => void): () => void {
  const h = (ev: MessageEvent) => {
    const d = ev.data as { source?: string; kind?: string; payload?: unknown } | null;
    if (!d || d.source !== CANVAS_SOURCE || !d.kind) return;
    fn({ kind: d.kind, payload: d.payload } as CanvasEvent);
  };
  window.addEventListener('message', h);
  return () => window.removeEventListener('message', h);
}

/* ─────────────────────────────── 起链 ─────────────────────────────── */

/** 起创作链（7 个角色 + 评审）。`manual_steps` = 每派一个角色停一次等人确认 ——
 *  右栏的「继续 / 打回」按钮**只有在这条开着的时候**有对象可点（链不挂起就没有
 *  决定可下，`hitl.decide` 会直接 400）。信箱那条不受它限制。 */
export function startChain(eid: string, manualSteps: boolean): Promise<unknown> {
  return http<unknown>('POST', `${P}/episodes/batch/storyboard/generate`,
    { episode_ids: [eid], manual_steps: manualSteps });
}

export function approveHitl(pid: string, body: Record<string, unknown>): Promise<unknown> {
  return http<unknown>('POST', `${P}/projects/${encodeURIComponent(pid)}/hitl`, body);
}

/* ─────────────────────────────── 小工具 ─────────────────────────────── */

const TERMINAL = ['ok', 'failed', 'incomplete', 'blocked', 'cancelled', 'lost'];

export function isRunning(status?: string): boolean {
  return !!status && !TERMINAL.includes(status);
}

export function hhmm(iso?: string): string {
  if (!iso) return '';
  const t = String(iso);
  const m = /T(\d\d:\d\d)/.exec(t);
  return m ? m[1] : t.slice(11, 16) || t;
}

/** 角色名的中文（右栏要显示"下一个派发给谁"）。 */
export const ROLE_ZH: Record<string, string> = {
  director: '制作规格',
  worldbuilder: '世界架构',
  assetdesigner: '资产设计',
  plotdesigner: '全剧目录',
  scriptwriter: '剧本',
  dialogue: '台词',
  scenedesigner: '分镜',
  reviewer: '评审',
};
