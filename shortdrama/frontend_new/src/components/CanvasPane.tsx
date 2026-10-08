/* ==========================================================================
   components/CanvasPane.tsx —— 中栏：复用 atelier 画布 + 镜头检查器
   --------------------------------------------------------------------------
   画布**不重写**：iframe 挂现成的 `/atelier/canvas?from=<接口>`，
   生产链每出一个资产，宿主把新节点集 postMessage 进去（桥见 `lib/studio.ts`
   与 `atelier/src/lib/pixa-bridge.ts`）。

   ★ 检查器是**浮层**，且只在选中某镜后出现。
     第一版它常驻在中栏底部，吃掉 145px 画布高度 —— 而 90% 的时间里它是
     "在画布上点一格静帧…"这句提示，什么都没在做。

   ★ 为什么"改分镜"放在这块浮层、而不是直接编辑画布节点：
     静帧节点 `s:LNxx` 的 `metadata.prompt` 是**组装后的静帧提示词**（含类型包
     风格块、参考图声明、反烧字条款），它**不等于**分镜表的「画面描述」列。
     把它写回 `visual` 会把整列污染成一段机器提示词，下一轮静帧就在错误指令上重画。
     ⇒ 画布负责"选中哪一格"（`pixa:select`），这里负责"改那一格的表内真值"。

   ⚠️ 「画面描述」初值取 `scenes[0].shots[0].content_plain`，**不是 `summary`** ——
     后者是 `_first_sentence(visual)`（`webmap.py:1227`），拿它回填再保存会把
     这一镜的描述**截成一句**。
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Segment } from '../types';
import { CANVAS_SOURCE, pingCanvas, pushCanvas, type CanvasDoc } from '../lib/studio';

const P = '/v1/pixa/short-drama';

type Props = {
  pid: string;
  name?: string;
  ep: number;
  doc: CanvasDoc | null;
  docError: string;
  segments: Segment[];
  shot: string;
  onShot: (shot: string) => void;
  onSave: (shot: string, patch: Record<string, unknown>) => Promise<string | null>;
  running: boolean;
  toolbar?: React.ReactNode;
};

/** 检查器可改的列。key = 后端 `EDITABLE_COLS` 的键。 */
const FIELDS: { key: string; label: string; multi?: boolean }[] = [
  { key: 'visual', label: '画面描述', multi: true },
  { key: 'dialogue', label: '对白', multi: true },
  { key: 'seconds', label: '时长(秒)' },
  { key: 'shot_type', label: '景别' },
  { key: 'camera', label: '运镜' },
  { key: 'scene', label: '场景' },
];

const segShot = (s: Segment) => String((s.v5 && s.v5.shot_name) || '');

/** ★ 全量画面描述：只认 `content_plain`，读不到就返回空串（**绝不拿 summary 凑数**）。 */
function fullVisual(s: Segment): string {
  const shots = (s.scenes?.[0] as { shots?: { content_plain?: string }[] } | undefined)?.shots
    ?? (s as { shots?: { content_plain?: string }[] }).shots;
  const plain = shots?.[0]?.content_plain;
  return typeof plain === 'string' && plain.trim() ? plain : '';
}

export function CanvasPane(p: Props) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const lastFp = useRef('');
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [canvasAlive, setCanvasAlive] = useState(false);
  const hasPid = !!p.pid && p.pid !== '-';

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

  // 握手重试：atelier 的加载时机不受我们控制（它 import 完还要 navigate 到
  // `/canvas/<id>`），漏掉一次 hello 就会永远停在"没连接"
  useEffect(() => {
    if (canvasAlive || !hasPid) return;
    const t = setInterval(() => pingCanvas(frame.current), 2000);
    return () => clearInterval(t);
  }, [canvasAlive, hasPid, p.ep]);

  // 指纹变了才推 —— 每 3 秒推一次会让几十张图全部重新解码
  useEffect(() => {
    if (!p.doc || !canvasAlive) return;
    if (p.doc.fingerprint === lastFp.current) return;
    lastFp.current = p.doc.fingerprint || '';
    pushCanvas(frame.current, p.doc);
  }, [p.doc, canvasAlive]);

  const seg = useMemo(() => p.segments.find((s) => segShot(s) === p.shot) || null,
    [p.segments, p.shot]);

  // 换镜 = 丢草稿。⛔ 不许把上一镜的文本带过来保存（那会把两镜写成一样）
  useEffect(() => { setDraft({}); setNote(null); }, [p.shot]);

  const initFor = (key: string): string => {
    if (!seg) return '';
    if (key === 'visual') return fullVisual(seg);
    if (key === 'seconds') return String(Math.round((seg.duration_ms || 0) / 1000) || '');
    const v5 = (seg.v5 || {}) as Record<string, unknown>;
    return String(v5[key] ?? (key === 'scene' ? String(seg.scene ?? '') : ''));
  };

  const dirty = FIELDS.filter((f) => draft[f.key] !== undefined && draft[f.key] !== initFor(f.key));

  const save = async () => {
    if (!p.shot || !dirty.length) return;
    setSaving(true); setNote(null);
    const patch: Record<string, unknown> = {};
    dirty.forEach((f) => { patch[f.key] = draft[f.key]; });
    const err = await p.onSave(p.shot, patch);
    setSaving(false);
    setNote(err || '已入表，该镜素材已作废');
    if (!err) setDraft({});
  };

  const apiUrl = `${location.origin}${P}/projects/${encodeURIComponent(p.pid)}/canvas?ep=${p.ep}`;
  const nodes = p.doc?.nodes.length || 0;

  return (
    <section className="stage">
      <div className="stage-bar">
        <span className="stage-title">{p.name || p.pid}</span>
        <span className="stage-meta"
              title={p.doc?.warnings?.join('\n') || ''}>
          {nodes} 格{p.running ? ' · 生产中' : ''}{!canvasAlive && hasPid ? ' · 未连接' : ''}
        </span>
        <span className="stage-spacer" />
        {p.toolbar}
      </div>

      <div className="stage-canvas">
        {/* 两种形态，按"这一集有没有产物"切：

            · **有产物** → `?from=<接口>`：画布是产物的**实时视图**，每出一张图长一格。
            · **没产物**（新项目/还没开工）→ **不传 `from=`，给它一块自由画布**：
              就是 atelier 原生那块（双击空白新建节点、能出图）。
              ★ 2026-10-08 用户问「为什么不可以新建空画布」—— 因为之前我把这一栏
                写死成"查看器"了；而 atelier 本身是**能画的**，锁掉它是我的选择，
                不是它的能力。空项目就该给一张能画的纸。

            ⚠️ 自由画布里的东西归**画布自己**（它存在浏览器里），**不会自动进**这个
               项目的产物目录 —— 要进项目得走"开工"那条路（角色产出 → 落盘 → 长格子）。
               这条别糊：所以下面那行字明说了。 */}
        {hasPid && nodes > 0 ? (
          <iframe key={p.pid + ':' + p.ep} title="画布"
                  src={`/atelier/canvas?from=${encodeURIComponent(apiUrl)}`} />
        ) : null}
        {(!hasPid || (nodes === 0 && !p.docError)) ? (
          <iframe key={'free:' + p.pid + ':' + p.ep} title="自由画布"
                  src="/atelier/canvas?go=last" />
        ) : null}

        {/* 还没选项目：中栏就是一张白纸（不是一句"到左栏选一个"）。
            用户原话：「为什么每次打开都是自动加载历史项目？可以变成直接加载空白画布吗？」
            —— 打开即白纸，然后跟导演聊、定意图、开工。 */}
        {p.docError ? (
          <div className="stage-empty">
            <strong>画布数据读不到</strong>
            <span className="err">{p.docError}</span>
          </div>
        ) : null}

        {/* 空项目的提示：**说人话**，别把给维护者看的话端上来。
            ⛔ 原文是 `canvasout` 的 warnings（含 `stills.json` /
            `SHORTDRAMA_VIDEO_REF_SOURCE` 这种变量名），那是排障用的，进 title 就够。 */}
        {!hasPid ? (
          <div className="stage-hint" title="跟导演聊完、点开工之后，这里会一格一格长出产物">
            这是一张白纸，可以直接画（双击空白处新建节点）。
            想让导演按你的想法生产，去右栏跟他说 —— 聊清楚了说「开工」。
          </div>
        ) : null}
        {hasPid && nodes === 0 && !p.docError ? (
          <div className="stage-hint" title={(p.doc?.warnings || []).join('\n')}>
            这一集还没有产物。下面这张画布**可以直接画**（双击空白处新建节点）；
            要让导演按 brief 生产，去右栏跟他说「开工」。
          </div>
        ) : null}

        {/* 浮层：只在选中某镜后出现 */}
        {p.shot ? (
          <div className="inspector">
            <h4>
              <span className="chip">{p.shot}</span>
              {seg ? <span className="muted">{seg.status === 'completed' ? '已出片' : (seg.v5?.job_state || '待生成')}</span> : null}
              <span className="stage-spacer" />
              <button type="button" className="btn btn--sm" onClick={() => p.onShot('')}>收起</button>
            </h4>
            {!seg ? (
              <p className="inspector-log err">分镜表里找不到 {p.shot}</p>
            ) : (
              <>
                {FIELDS.map((f) => (
                  <div className={f.multi ? 'field' : 'field-row'} key={f.key}
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
                    {saving ? <span className="spin" /> : `写入分镜表${dirty.length ? ` ${dirty.length}` : ''}`}
                  </button>
                  {dirty.length ? (
                    <button type="button" className="btn btn--sm" onClick={() => setDraft({})}>放弃</button>
                  ) : null}
                </div>
                {note ? <div className="inspector-log">{note}</div> : null}
              </>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
