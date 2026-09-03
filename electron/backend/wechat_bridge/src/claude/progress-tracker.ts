/**
 * 进度观察器 —— 把 AG-UI 的 CUSTOM 事件翻译成微信里能读的短句。
 *
 * 背景（2026-09-01 修）：微信桥的 `handleAgUiEvent` 只认 TEXT_MESSAGE_* 和
 * RUN_*，`CUSTOM` 一律 `default: break` 丢掉。于是只要 agent 调 langgraph_agent
 * 跑长任务，外层的文本流就是空的 —— 微信侧除了一句「稍后再等」的保活文案
 * 什么都看不到。本模块负责把那条被丢掉的 CUSTOM 通道接回来。
 *
 * 职责边界：
 *  - 只做「事件 → 短句」的翻译 + 去重 / 节流 / 累计，**不做任何 I/O**。
 *  - 推送由调用方（main.ts）拿到 ProgressLine 后走既有的 emitText 链路。
 *  - 送达结果由调用方回写（markDelivered / noteSendFailure），用于构造
 *    「你不在的时候发生了什么」的补发摘要。
 *
 * 纯逻辑、无依赖，可被 node:test 直接单测（见 src/tests/progress-tracker.test.ts）。
 */

export type ProgressKind =
  | 'started'
  | 'progress'
  | 'artifact'
  | 'approval'
  | 'error'
  | 'done';

export interface ProgressEntry {
  ts: number;
  kind: ProgressKind;
  /** 去重键：同一节点同一状态在一个 TTL 内只推送一次 */
  key: string;
  text: string;
  /** 是否绕过节流立刻推送（审批 / 错误 / 完成 / 产物） */
  urgent: boolean;
  /** 是否被选中推送（false = 被去重/节流挡下，仍记在账上供 /进度 与补发摘要用） */
  emitted: boolean;
  /** 是否已成功送达微信 */
  delivered: boolean;
  /** 是否尝试过推送但失败（回复窗口关闭的判据） */
  failed: boolean;
}

export interface ProgressLine {
  text: string;
  entry: ProgressEntry;
}

interface Humanized {
  kind: ProgressKind;
  key: string;
  text: string;
  urgent: boolean;
  /** 只更新「当前步骤」、不进聊天流的事件（如 workflow.trace / 完成态进度） */
  trackStep?: string;
  /** trace 事件带的原始节点名，仅作 currentStep 的兜底来源 */
  trackNode?: string;
}

// ---------------------------------------------------------------------------
// 调参
// ---------------------------------------------------------------------------

const MAX_ENTRIES = 80;
/** 同一 key 多久内不重复推送 */
const DEDUP_TTL_MS = 45_000;
/** 非紧急进度两条之间的最短间隔 */
const THROTTLE_MS = 20_000;
/**
 * 单轮最多推送多少条进度。iLink 每个 context_token 只有 ~11 条被动回复额度，
 * 进度刷太猛会把最终答案挤掉，所以这里刻意压得很紧；被挡下的进度仍然记在
 * 账上，由 /进度 和「下一条消息时的补发摘要」兜住。
 */
const MAX_EMITS_PER_RUN = 6;
const DIGEST_LIMIT = 8;
const REPORT_LIMIT = 10;
/** 补发摘要只回看这么久以内的进度 */
const DIGEST_LOOKBACK_MS = 6 * 60 * 60 * 1000;

/**
 * 工具参数摘要时优先取哪些键 —— 覆盖常见的搜索/读写/执行类参数名。
 * 找不到命中时退回「第一个像人话的字符串值」，不做任何 per-tool 硬编码表。
 */
const ARG_KEY_PRIORITY = [
  'query', 'q', 'keyword', 'search', 'topic', 'question',
  'url', 'command', 'cmd', 'script',
  'path', 'file', 'filename', 'filepath',
  'prompt', 'task', 'input', 'text', 'content', 'code', 'pattern', 'message',
];

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function truncate(s: string, max: number): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
}

/** base64 / dataURL / 超长无空格串 —— 摘要里出现这种就是乱码，直接跳过 */
function looksLikeBinary(s: string): boolean {
  return /^data:/i.test(s) || /^[A-Za-z0-9+/=\s]{120,}$/.test(s);
}

/**
 * 把工具调用的 args JSON 缩成一行人话：优先按 ARG_KEY_PRIORITY 取键，
 * 否则取第一个「像人话」的字符串/数值；解析失败就截原始文本。
 */
function summarizeArgs(argsText?: unknown): string {
  const raw = String(argsText || '').trim();
  if (!raw) return '';
  try {
    const obj = JSON.parse(raw);
    if (obj === null || typeof obj !== 'object') return truncate(String(obj), 48);
    if (Array.isArray(obj)) {
      const first = obj[0];
      if (typeof first === 'string' && !looksLikeBinary(first)) return truncate(first, 48);
      return truncate(JSON.stringify(obj), 48);
    }
    const rec = obj as Record<string, unknown>;
    for (const k of ARG_KEY_PRIORITY) {
      const val = rec[k];
      if (typeof val === 'string' && val.trim() && !looksLikeBinary(val)) return truncate(val, 48);
    }
    for (const val of Object.values(rec)) {
      if (typeof val === 'string' && val.trim() && !looksLikeBinary(val)) return truncate(val, 48);
      if (typeof val === 'number' || typeof val === 'boolean') return truncate(String(val), 48);
    }
    return '';
  } catch {
    return truncate(raw, 48);
  }
}

function shortPath(p?: string): string {
  if (!p) return '';
  const norm = String(p).replace(/\\/g, '/');
  const idx = norm.lastIndexOf('/');
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}

function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.round(ms / 1000));
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min <= 0) return `${sec} 秒`;
  return `${min} 分 ${sec} 秒`;
}

// ---------------------------------------------------------------------------
// 事件 → 短句（纯函数，单测主入口）
// ---------------------------------------------------------------------------

/**
 * 把一个 AG-UI `CUSTOM` 事件翻译成一条人话。返回 null 表示「这条不用管」
 * （拓扑、思考 token、工具原始输出等噪音）。
 */
export function humanizeEvent(
  ev: { name?: string; value?: unknown } | null | undefined,
): Humanized | null {
  const name = String(ev?.name || '');
  if (!name) return null;
  const v = (ev?.value && typeof ev.value === 'object' ? ev.value : {}) as Record<string, any>;

  if (name.startsWith('workflow.')) {
    switch (name) {
      // 图拓扑，给前端画 DAG 用的，进聊天流纯属噪音
      case 'workflow.graph':
        return null;

      case 'workflow.started':
        return { kind: 'started', key: 'wf:started', text: '🚀 任务已开始', urgent: false };

      // 节点级 trace 是**不去重**的（series 模式会重复跑同一节点），只拿来
      // 更新「当前节点」，绝不逐条推送。
      case 'workflow.trace': {
        const node = String(v.node || '');
        if (!node) return null;
        return { kind: 'progress', key: `trace:${node}`, text: '', urgent: false, trackNode: node };
      }

      case 'workflow.progress': {
        const status = String(v.status || '');
        const stepId = String(v.step_id || v.node || '');
        const label = String(v.stage || stepId || '处理中');
        const completed = Number(v.completed || 0);
        const total = Number(v.total || 0);
        const counter = completed > 0 && total > 0 ? `${completed}/${total} ` : '';
        if (status === 'done') {
          return {
            kind: 'progress',
            key: `done:${stepId}`,
            text: `✅ ${counter}${label} 完成`,
            urgent: false,
            trackStep: label,
          };
        }
        return {
          kind: 'progress',
          key: `run:${stepId}`,
          text: `⏳ ${counter}${label}`,
          urgent: false,
          trackStep: `${counter}${label}`.trim(),
        };
      }

      case 'workflow.artifact': {
        const label = String(v.label || v.id || '产物');
        const file = shortPath(v.path);
        return {
          kind: 'artifact',
          key: `artifact:${String(v.id || label)}`,
          text: file ? `📦 ${label}：${file}` : `📦 ${label} 已生成`,
          urgent: true,
        };
      }

      case 'workflow.approval': {
        const label = truncate(String(v.label || v.message || '需要确认'), 120);
        return {
          kind: 'approval',
          key: `approval:${String(v.gate_id || v.node || label)}`,
          text: `🙋 需要你确认：${label}\n回复「批准」继续，回复「拒绝」中止。`,
          urgent: true,
        };
      }

      case 'workflow.error': {
        const msg = truncate(String(v.message || '未知错误'), 200);
        return {
          kind: 'error',
          key: `error:${String(v.node || '')}:${msg.slice(0, 40)}`,
          text: `⚠️ ${msg}`,
          urgent: true,
        };
      }

      case 'workflow.done': {
        const status = String(v.status || 'done');
        if (status === 'rejected') {
          return { kind: 'done', key: 'wf:done', text: '⏹ 已按你的决定中止', urgent: true };
        }
        if (status === 'timeout') {
          return { kind: 'done', key: 'wf:done', text: '⌛ 审批等待超时，任务已中止', urgent: true };
        }
        if (status === 'done') {
          return { kind: 'done', key: 'wf:done', text: '✅ 任务完成', urgent: true };
        }
        return {
          kind: 'done',
          key: 'wf:done',
          text: `❌ 任务失败${v.error ? `：${truncate(String(v.error), 160)}` : ''}`,
          urgent: true,
        };
      }

      default:
        return null;
    }
  }

  // ── 真实工作过程（2026-09-03 起）─────────────────────────────────
  // 用户明确反馈「不要礼貌但无用的状态占位」：stream.phase（思考中/生成回复/
  // 执行工具）这种空转心跳从推送往返为 null —— 它们不携带任何任务信息。
  // 普通聊天（非 workflow）长任务里唯一「真实」的过程信号是工具调用本身：
  // 搜了什么关键词、跑了什么命令、打开了哪个文件。
  if (name === 'tool.call') {
    const toolName = String(v.toolName || 'tool');
    const excerpt = summarizeArgs(v.argsText);
    return {
      kind: 'progress',
      key: `tool:${toolName}:${excerpt}`,
      text: excerpt ? `🔧 ${toolName}：${excerpt}` : `🔧 ${toolName}`,
      urgent: false,
      // 即使被节流挡下，keepalive 和 /进度 也该知道现在在跑什么
      trackStep: excerpt ? `${toolName}（${excerpt}）` : toolName,
    };
  }

  if (name === 'tool.error') {
    const toolName = String(v.toolName || 'tool');
    return {
      kind: 'error',
      key: `toolerr:${toolName}`,
      text: `⚠️ ${toolName} 执行失败`,
      urgent: true,
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Per-thread 累加器
// ---------------------------------------------------------------------------

export class ProgressTracker {
  readonly threadId: string;

  private entries: ProgressEntry[] = [];
  private lastKeyAt = new Map<string, number>();
  private lastEmitAt = 0;
  private emitCount = 0;
  private sendFailed = false;

  /** 最近一次 workflow.progress 报的「N/M 步骤名」——最像「现在在干嘛」的东西 */
  private currentStep = '';
  /** workflow.trace 给的原始节点名，仅当 currentStep 为空时兜底 */
  private currentNode = '';
  private startedAt = Date.now();
  lastActivityAt = Date.now();

  constructor(threadId: string) {
    this.threadId = threadId;
  }

  /** 新一轮对话开始时调用，清掉上一轮的状态。 */
  reset(): void {
    this.entries = [];
    this.lastKeyAt.clear();
    this.lastEmitAt = 0;
    this.emitCount = 0;
    this.sendFailed = false;
    this.currentStep = '';
    this.currentNode = '';
    this.startedAt = Date.now();
    this.lastActivityAt = Date.now();
  }

  /** 当前步骤的可读文案；没有则用 trace 节点名兜底。 */
  currentStepText(): string {
    return this.currentStep || this.currentNode || '';
  }

  get elapsedMs(): number {
    return Date.now() - this.startedAt;
  }

  /**
   * 吃进一个 CUSTOM 事件。返回非 null 表示「这条值得推给微信」，调用方负责推。
   * 无论返回什么都已记账（/进度 与补发摘要看得到）。
   */
  ingest(ev: { name?: string; value?: unknown } | null | undefined): ProgressLine | null {
    this.lastActivityAt = Date.now();

    const h = humanizeEvent(ev);
    if (!h) return null;

    // trace 之类只有 track 信息、没有文案的事件：只更新当前节点。
    if (!h.text) {
      if (h.trackNode) this.currentNode = h.trackNode;
      return null;
    }
    if (h.trackStep) this.currentStep = h.trackStep;

    const now = Date.now();
    const entry: ProgressEntry = {
      ts: now,
      kind: h.kind,
      key: h.key,
      text: h.text,
      urgent: h.urgent,
      emitted: false,
      delivered: false,
      failed: false,
    };
    this.entries.push(entry);
    if (this.entries.length > MAX_ENTRIES) this.entries.shift();

    const lastAt = this.lastKeyAt.get(h.key) || 0;
    if (now - lastAt < DEDUP_TTL_MS) return null;

    if (!h.urgent) {
      if (this.emitCount >= MAX_EMITS_PER_RUN) return null;
      if (now - this.lastEmitAt < THROTTLE_MS) return null;
    }

    this.lastKeyAt.set(h.key, now);
    this.lastEmitAt = now;
    this.emitCount += 1;
    entry.emitted = true;
    return { text: h.text, entry };
  }

  markDelivered(entry: ProgressEntry): void {
    entry.delivered = true;
    entry.failed = false;
  }

  noteSendFailure(entry?: ProgressEntry): void {
    this.sendFailed = true;
    if (entry) entry.failed = true;
  }

  /** 上一轮推送是否失败过 —— 决定要不要在下一条用户消息前补一段进展摘要。 */
  get hadSendFailure(): boolean {
    return this.sendFailed;
  }

  hasDigest(): boolean {
    if (!this.sendFailed) return false;
    const cutoff = Date.now() - DIGEST_LOOKBACK_MS;
    return this.entries.some((e) => e.ts >= cutoff);
  }

  /** 记账里所有条目（供 /进度 渲染）。 */
  allEntries(): readonly ProgressEntry[] {
    return this.entries;
  }

  /**
   * 生成「你不在的时候发生了什么」的补发摘要，并把它标记为已消费。
   * 只在至少一次推送失败过（= 回复窗口被关掉过）时才有内容。
   */
  takeDigest(): string | null {
    if (!this.hasDigest()) return null;
    const lines = this.pickDigestLines();
    // 消费掉：摘要一旦给出就不再重复
    this.sendFailed = false;
    for (const e of this.entries) e.failed = false;
    if (!lines.length) return null;
    return [
      '📌 上一条任务的部分进展没能在当时送达微信，补给你：',
      ...lines.map((l) => `· ${l}`),
    ].join('\n');
  }

  private pickDigestLines(): string[] {
    const cutoff = Date.now() - DIGEST_LOOKBACK_MS;
    const byKey = new Map<string, ProgressEntry>();
    for (const e of this.entries) {
      if (e.ts < cutoff) continue;
      byKey.set(e.key, e); // 同 key 保留最新一条
    }
    return [...byKey.values()]
      .sort((a, b) => a.ts - b.ts)
      .slice(-DIGEST_LIMIT)
      .map((e) => e.text.split('\n')[0]);
  }

  /** /进度 用：当前步骤 + 最近 N 条。 */
  renderReport(): string {
    const step = this.currentStepText();
    if (!step && this.entries.length === 0) {
      return '📊 当前没有进行中的任务。';
    }

    const byKey = new Map<string, ProgressEntry>();
    for (const e of this.entries) byKey.set(e.key, e);
    const recent = [...byKey.values()]
      .sort((a, b) => a.ts - b.ts)
      .slice(-REPORT_LIMIT)
      .map((e) => `· ${e.text.split('\n')[0]}`);

    const lines: string[] = ['📊 当前进展'];
    lines.push(step ? `正在做：${step}` : '正在做：—');
    if (recent.length) {
      lines.push('', '最近动态：', ...recent);
    }
    lines.push('', `已运行 ${formatDuration(this.elapsedMs)}`);
    return lines.join('\n');
  }
}

// ---------------------------------------------------------------------------
// Registry（main.ts / handlers.ts 共用）
// ---------------------------------------------------------------------------

const REGISTRY_MAX = 64;
const registry = new Map<string, ProgressTracker>();

export function getProgressTracker(threadId: string): ProgressTracker {
  let t = registry.get(threadId);
  if (!t) {
    // 简单的 FIFO 淘汰，防止长期运行下无限增长
    if (registry.size >= REGISTRY_MAX) {
      const oldest = registry.keys().next().value;
      if (oldest !== undefined) registry.delete(oldest);
    }
    t = new ProgressTracker(threadId);
    registry.set(threadId, t);
  }
  return t;
}

/** 新一轮对话：换一个干净的 tracker。 */
export function resetProgressTracker(threadId: string): ProgressTracker {
  registry.delete(threadId);
  const t = new ProgressTracker(threadId);
  registry.set(threadId, t);
  return t;
}

/** 「wx-<userId>」这种 threadId 约定集中在这里，避免各处拼字符串。 */
export function wechatThreadId(fromUserId: string): string {
  return `wx-${fromUserId}`;
}
