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
  /** 信箱消息可定向的角色（含 director）*/
  targets: string[];
  /** 链没挂起时「让导演重做」可选的角色 —— 7 个被派发的角色，不含 director */
  idle_targets?: string[];
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

/* ─────────────────────────────── 和导演对话 ─────────────────────────────── */

/** `system` = 后端落进时间线的**盘上事实**（如"这一轮创作链收工：failed + 理由"）。 */
export type ChatTurn = { role: 'user' | 'director' | 'system'; text: string; at?: string; ep?: number };

export type ChatState = {
  turns: ChatTurn[];
  busy: boolean;
  status: string;
  thread_id: string;
  error?: string;
};

export function getChat(pid: string, ep: number): Promise<ChatState> {
  return http<ChatState>('GET',
    `${P}/projects/${encodeURIComponent(pid)}/director/chat?ep=${ep}`);
}

export type AskResult = { ok: boolean; started?: boolean; run?: { run_id?: string } };
/** 说一句。`started: true` = 这句话本身就是"开工"，后端已经起链（**不用再点按钮**）。 */
export function askDirector(pid: string, text: string, ep: number): Promise<AskResult> {
  return http<AskResult>('POST',
    `${P}/projects/${encodeURIComponent(pid)}/director/chat`, { text, ep });
}

/** **空白项目**：不调模型、秒级建壳（名字 + 包 + 集数 + 画幅），brief 的创作字段留空。 */
export function createBlank(name: string, pack: string, episodes: number,
                            ratio: string): Promise<{ pid: string; topic: string }> {
  return http<{ pid: string; topic: string }>('POST', `${P}/projects/blank`,
    { name, pack, episodes, ratio });
}

/** **开工**：接着刚才那段对话跑完整条创作链（生产与聊天同一条时间线）。 */
export function startProduction(pid: string, ep: number, manualSteps: boolean,
                                imageVendor = '', videoVendor = ''): Promise<unknown> {
  return http<unknown>('POST',
    `${P}/projects/${encodeURIComponent(pid)}/director/start`,
    { ep, manual_steps: manualSteps, image_vendor: imageVendor, video_vendor: videoVendor });
}

/* ─────────────────────────────── 起链 ─────────────────────────────── */

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
