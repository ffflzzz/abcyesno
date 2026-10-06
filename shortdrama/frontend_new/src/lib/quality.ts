/* ==========================================================================
   src/lib/quality.ts —— 「自动质检」开关
   --------------------------------------------------------------------------
   管的是**质检自愈**那两个环路：
     · 静帧质检（still_qc）—— 判出硬伤就**自动重画**静帧（默认最多 3 轮）
     · 成片复核（clip_qc） —— 抽帧复核不合格就**自动重拍**（默认 2 轮）

   为什么默认**关**（这与"后端默认开"不矛盾，是**路径**不同）：
     这两条环路是"机器替人判断画面合不合格，并**直接烧配额去改**"。
     前端这条路的前提是**判断权在人**；而 CLI / 外部 agent 走的是另一条路径，
     那边**没人看片**，所以照旧默认开。
     ⇒ 开关跟着**路径**走，不跟着机器走：值随请求体传给后端，后端只写子进程 env。
   ========================================================================== */

const KEY = 'sd.quality.v1';

/** 默认全关（人工模式） */
const state = { still: false, clip: false };

function load(): void {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || '{}') as { still?: boolean; clip?: boolean };
    state.still = !!s.still;
    state.clip = !!s.clip;
  } catch { /* 坏数据当默认值用，别让设置页打不开 */ }
}
load();

function save(): void {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* 隐私模式等 */ }
}

export type QcKey = 'still' | 'clip';

/** 开关当前值。 */
export function qcOn(k: QcKey): boolean { return !!state[k]; }

/** 翻转一格，返回新值。 */
export function qcToggle(k: QcKey): boolean {
  if (!(k in state)) return false;
  state[k] = !state[k];
  save();
  return state[k];
}

/**
 * 放进**生成类请求体**的那两个字段。
 *
 * ★ 必须是 `1`/`0` 而不是 `true`/`false`：后端 `runner._qc_flag` 认的是
 *   "数字/字符串 0/1 + 布尔 + 若干假词"，发数字最不容易两边理解不一致。
 */
export function qcPayload(): { still_qc: number; clip_qc: number } {
  return { still_qc: state.still ? 1 : 0, clip_qc: state.clip ? 1 : 0 };
}

export const QC_LABELS: { key: QcKey; label: string; hint: string }[] = [
  { key: 'still', label: '静帧', hint: '打开：判出硬伤就自动重画静帧（最多 3 轮，烧生图配额）' },
  { key: 'clip', label: '成片', hint: '打开：抽帧复核不合格就自动重拍（最多 2 轮，烧视频配额）' },
];

export const Quality = { on: qcOn, toggle: qcToggle, payload: qcPayload, state };
