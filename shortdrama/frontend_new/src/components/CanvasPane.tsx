/* ==========================================================================
   components/CanvasPane.tsx —— 中栏：复用 atelier 画布 + 镜头检查器
   --------------------------------------------------------------------------
   画布**不重写**：iframe 挂现成的 `/atelier/canvas?from=<接口>`，
   生产链每出一个资产，宿主把新节点集 postMessage 进去（桥见 `lib/studio.ts`
   与 `atelier/src/lib/pixa-bridge.ts`）。

   ★ 为什么"改分镜"放在这块检查器、而不是直接编辑画布节点：
     静帧节点的 `metadata.prompt` 是**组装后的静帧提示词**（含类型包风格块、
     参考图声明、反烧字条款），它**不等于**分镜表的「画面描述」列。把节点文本
     写回 `visual` 会把整列污染成一段机器提示词，下一轮静帧就在错误指令上重画。
     ⇒ 画布负责"选中哪一格"（`pixa:select`），这里负责"改那一格的表内真值"。

   ⚠️ 「画面描述」的初值取 `scenes[0].shots[0].content_plain`，**不是 `summary`** ——
     后者是 `_first_sentence(visual)`（`webmap.py:1227`），拿它回填再保存会把
     这一镜的描述**截成一句**。
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Segment } from '../types';
import { CANVAS_SOURCE, pingCanvas, pushCanvas, type CanvasDoc } from '../lib/studio';

const P = '/v1/pixa/short-drama';

type Props = {
  pid: string;
  ep: number;
  doc: CanvasDoc | null;
  docError: string;
  segments: Segment[];
  shot: string;
  onShot: (shot: string) => void;
  onSave: (shot: string, patch: Record<string, unknown>) => Promise<string | null>;
  running: boolean;
  nodeCount: number;
  /** 宿主控制条（集号 / 开始生产 / 逐步确认）。渲染在 `.stage-bar` 里 ——
   *  ⛔ 不要用 `position: fixed` 悬浮：实测那样会**压在画布自己的顶栏上**，
   *  两边文字叠成一片读不出（第一版就是这样）。控件要待在它治理的那一栏里。 */
  toolbar?: React.ReactNode;
};

/** 检查器可改的列。key = 后端 `EDITABLE_COLS` 的键，取不到就别给。 */
const FIELDS: { key: string; label: string; multi?: boolean }[] = [
  { key: 'visual', label: '画面描述', multi: true },
  { key: 'dialogue', label: '对白', multi: true },
  { key: 'seconds', label: '时长(秒)' },
  { key: 'shot_type', label: '景别' },
  { key: 'camera', label: '运镜' },
  { key: 'scene', label: '场景' },
];

function segShot(s: Segment): string {
  return String((s.v5 && s.v5.shot_name) || '');
}

/** ★ 全量画面描述：只认 `content_plain`，读不到就返回空串（**绝不拿 summary 凑数**）。 */
function fullVisual(s: Segment): string {
  const sc = (s.scenes && s.scenes[0]) as { shots?: { content_plain?: string }[] } | undefined;
  const sh = sc && sc.shots && sc.shots[0];
  const plain = sh && sh.content_plain;
  if (typeof plain === 'string' && plain.trim()) return plain;
  // content_rich 是 token 串（`@资产名` 那种），它也可能带全文；仍读它的 plain 变体
  const alt = (s as { shots?: { content_plain?: string }[] }).shots &&
    (s as { shots?: { content_plain?: string }[] }).shots![0];
  return typeof alt?.content_plain === 'string' ? alt.content_plain : '';
}

export function CanvasPane(p: Props) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const lastFp = useRef('');
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [canvasAlive, setCanvasAlive] = useState(false);

  // 画布 → 宿主：选中哪一格 / 画布活着 / 现在几格
  useEffect(() => {
    const h = (ev: MessageEvent) => {
      const d = ev.data as { source?: string; kind?: string; payload?: { shot?: string } } | null;
      if (!d || d.source !== CANVAS_SOURCE) return;
      if (d.kind === 'pixa:hello' || d.kind === 'pixa:ready') setCanvasAlive(true);
      if (d.kind === 'pixa:select') {
        const s = String(d.payload?.shot || '');
        if (s) p.onShot(s);
      }
    };
    window.addEventListener('message', h);
    return () => window.removeEventListener('message', h);
  }, [p]);

  // 桥握手：画布发 `pixa:hello`，宿主回 `pixa:ping` 确认并**每 2 秒重试到握手为止**。
  // 为什么要重试：iframe 里 atelier 的加载时机不受我们控制（它 import 完还要
  // navigate 到 `/canvas/<id>`），漏掉一次 hello 就会永远停在「画布未连接」。
  useEffect(() => {
    if (canvasAlive || !p.pid || p.pid === '-') return;
    const t = setInterval(() => pingCanvas(frame.current), 2000);
    return () => clearInterval(t);
  }, [canvasAlive, p.pid, p.ep]);

  // 指纹变了才推 —— 每 3 秒推一次会让 30 张图全部重新解码
  useEffect(() => {
    if (!p.doc || !canvasAlive) return;
    if (p.doc.fingerprint === lastFp.current) return;
    lastFp.current = p.doc.fingerprint || '';
    pushCanvas(frame.current, p.doc);
  }, [p.doc, canvasAlive]);

  const seg = useMemo(
    () => p.segments.find((s) => segShot(s) === p.shot) || null,
    [p.segments, p.shot],
  );

  // 换镜 = 丢草稿。⛔ 不许把上一镜的文本带过来保存（那会把两镜写成一样）
  useEffect(() => { setDraft({}); setNote(null); }, [p.shot]);

  const initFor = (key: string): string => {
    if (!seg) return '';
    if (key === 'visual') return fullVisual(seg);
    if (key === 'seconds') return String(Math.round((seg.duration_ms || 0) / 1000) || '');
    const v5 = (seg.v5 || {}) as Record<string, unknown>;
    const fallback = key === 'scene' ? String(seg.scene ?? '') : '';
    return String(v5[key] ?? fallback);
  };

  const dirty = FIELDS.filter((f) => draft[f.key] !== undefined && draft[f.key] !== initFor(f.key));

  const save = async () => {
    if (!p.shot || !dirty.length) return;
    setSaving(true); setNote(null);
    const patch: Record<string, unknown> = {};
    dirty.forEach((f) => { patch[f.key] = draft[f.key]; });
    const err = await p.onSave(p.shot, patch);
    setSaving(false);
    setNote(err || `已入表：${dirty.map((f) => f.label).join('、')} · 该镜静帧/片段已作废`);
    if (!err) setDraft({});
  };

  const apiUrl = `${location.origin}${P}/projects/${encodeURIComponent(p.pid)}/canvas?ep=${p.ep}`;

  return (
    <section className="stage">
      <div className="stage-bar">
        <span className="stage-title">{p.pid}</span>
        <span className="chip">第 {p.ep} 集</span>
        <span className="stage-meta">
          画布 {p.nodeCount || p.doc?.nodes.length || 0} 格
          {p.running ? ' · 生产进行中' : ''}
          {canvasAlive ? '' : ' · 画布未连接'}
        </span>
        <span className="stage-spacer" />
        {p.toolbar}
        {p.doc?.warnings?.length ? (
          <span className="chip chip--warn" title={p.doc.warnings.join('\n')}>
            {p.doc.warnings.length} 条画布提示</span>
        ) : null}
      </div>

      <div className="stage-canvas">
        {/* key 绑 (pid, ep)：换项目/换集就整块重挂，让 atelier 重新 import 一份。
            ⛔ pid 还没选定时**不要**挂 iframe —— 第一版用 '-' 占位，于是每次开页
            都发一条 `projects/-/canvas` 的 404 进控制台，把真错误淹掉。 */}
        {p.pid && p.pid !== '-' ? (
          <iframe key={p.pid + ':' + p.ep} title="画布"
                  src={`/atelier/canvas?from=${encodeURIComponent(apiUrl)}`} />
        ) : (
          <div className="stage-empty"><span>到左栏选一个项目</span></div>
        )}
        {!p.doc && !p.docError ? (
          <div className="stage-empty"><span className="spin" /><span>正在读盘上的产物…</span></div>
        ) : null}
        {p.docError ? (
          <div className="stage-empty">
            <strong>画布数据读不到</strong>
            <span className="err">{p.docError}</span>
            <span className="inspector-hint">这一集可能还没出静帧 —— 先点「开始生产」，或到左栏换一集。</span>
          </div>
        ) : null}
        {p.doc && !p.doc.nodes.length ? (
          <div className="stage-empty">
            <strong>这一集画布是空的</strong>
            <span className="inspector-hint">{p.doc.warnings?.[0] || '盘上没有静帧、片段、定妆照或成片。'}</span>
          </div>
        ) : null}
      </div>

      <div className="inspector">
        <h4>镜头检查器 {p.shot ? <span className="chip">{p.shot}</span> : null}</h4>
        {!p.shot ? (
          <p className="inspector-hint">在画布上点一格静帧，这里就切到那一镜，改完点「写入分镜表」。
            改动会记进台账，导演下一轮派发时读得到。</p>
        ) : !seg ? (
          <p className="inspector-hint">分镜表里找不到 {p.shot} —— 表可能已被重写或还没生成。</p>
        ) : (
          <>
            <p className="inspector-hint">
              改哪列就只写哪列；没动过的列一律不提交（⛔ 不会把别的列顺手改掉）。
            </p>
            {FIELDS.map((f) => (
              <div className={'field' + (f.multi ? '' : ' field--row')} key={f.key}
                   style={f.multi ? undefined : { display: 'flex', gap: 8 }}>
                {f.multi ? (
                  <>
                    <label>{f.label}</label>
                    <textarea rows={f.key === 'visual' ? 4 : 2}
                              value={draft[f.key] !== undefined ? draft[f.key] : initFor(f.key)}
                              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
                  </>
                ) : (
                  <div>
                    <label>{f.label}</label>
                    <input value={draft[f.key] !== undefined ? draft[f.key] : initFor(f.key)}
                           onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
                  </div>
                )}
              </div>
            ))}
            <div className="inspector-actions">
              <button type="button" className="btn btn--sm btn--primary"
                      disabled={!dirty.length || saving} onClick={() => void save()}>
                {saving ? <span className="spin" /> : `写入分镜表${dirty.length ? ` (${dirty.length})` : ''}`}
              </button>
              {dirty.length ? (
                <button type="button" className="btn btn--sm" onClick={() => setDraft({})}>放弃改动</button>
              ) : null}
              <span className="chip">{seg.status === 'completed' ? '已出片' : (seg.v5?.job_state || seg.status || '待生成')}</span>
            </div>
            {note ? <div className="inspector-log">{note}</div> : null}
          </>
        )}
      </div>
    </section>
  );
}
