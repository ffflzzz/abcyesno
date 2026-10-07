/* ==========================================================================
   components/DirectorChat.tsx —— 右栏：与 supervisor / director 对话
   --------------------------------------------------------------------------
   ★ 两条通道，**按链此刻的状态自动选**（这是设计要点，不是兜底）：
     · 链**正挂在步级门上**（`hitl.pending`）→ 「继续 / 打回 / 中止」，走
       `hitl.decide`，立刻生效，能带自由文字理由；
     · 链**在跑或没跑** → 信箱，落盘排队，下一个被派发的角色读到（`v5/inbox.py`）。
   ⛔ 界面不许说"已发送给导演"而实际只是排进队列 —— 每条自己的消息显示真实状态
      （`排队中` / `已送达 分镜`），读的是盘上 `delivered_to`，不是猜的。

   ★ 文案砍到名词级（2026-10-06 对着参考图量过）：第一版这里最长一句 39 字、
     空态是一段 33 字的说明。参考图右栏全部文案最长 12 字。
     ⇒ 解释一律进 `title`，界面只留名词和状态。
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { InboxState } from '../lib/studio';
import { ROLE_ZH, hhmm } from '../lib/studio';

type Props = {
  inbox: InboxState | null;
  error: string;
  busy: boolean;
  /** 返回 `{err, id}`：`id` 是后端给的信箱编号，宿主用它把这条认成「我说的」。
   *  ⛔ 不能靠 `by` 判断 —— 那是显示名，换个标签同一条消息就会跑到对面去。 */
  onSend: (text: string, to: string) => Promise<{ err: string | null; id?: number }>;
  onDecide: (decision: string, target: string, note: string) => Promise<string | null>;
  onRedo: (target: string, note: string) => Promise<string | null>;
  mine: number[];
  manualSteps: boolean;
  onManualSteps: (v: boolean) => void;
};

export function DirectorChat({ inbox, error, busy, onSend, onDecide, onRedo, mine,
  manualSteps, onManualSteps }: Props) {
  const [text, setText] = useState('');
  const [to, setTo] = useState('');
  const [note, setNote] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);

  const h = inbox?.hitl;
  const pending = !!h?.pending;
  /** ★ 两个下拉是**两份不同的名单**，不许合并：
   *    · 挂起时的「打回」目标 = `hitl.redo_targets`（链落盘的那份，含 `director`，
   *      因为第一停时唯一能审的就是制作规格）
   *    · 没挂起时的「让导演重做」目标 = `idle_targets`（7 个被派发的角色，
   *      ⛔ 不含 `director` —— 打回它 = 重写规格 + 全部 7 个角色重做，是全项目重置）
   *  第一版这里回落到 `inbox.targets`（那是**信箱消息**的定向名单，含 director），
   *  于是重做按钮的**默认值就是最毁的那一项**。2026-10-07 验收脚本点了一下，
   *  就把一个 9 月项目的 8 份产物全挪进 `.rerun_backup/`。 */
  const gateTargets = useMemo(() => (h?.redo_targets || []).filter(Boolean), [h]);
  const idleTargets = useMemo(() => (inbox?.idle_targets || []).filter(Boolean), [inbox]);
  const [target, setTarget] = useState('');
  const [gateTarget, setGateTarget] = useState('');

  useEffect(() => {
    if (pending && !gateTargets.includes(gateTarget)) setGateTarget(String(gateTargets[0] || ''));
  }, [pending, gateTargets, gateTarget]);

  const stream = useMemo(
    () => [...(inbox?.messages || []), ...(inbox?.edits || [])].sort((a, b) => a.id - b.id),
    [inbox]);

  useEffect(() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; }, [stream.length]);

  const send = async () => {
    const t = text.trim();
    if (!t) return;
    setMsg(null);
    const r = await onSend(t, to);
    if (r.err) { setMsg(r.err); return; }
    setText('');
  };

  return (
    <aside className="chat">
      <div className="chat-head">
        <span className="rail-title">导演</span>
        <span className="chat-state">
          {pending ? '等你确认' : inbox ? (manualSteps ? '逐步停' : '一路跑') : ''}
        </span>
        <span className="stage-spacer" />
        <label className="chat-state" title="每派一个角色前停一次，等人点继续 / 打回。翻了要重启 dev server、只对下一次生成生效">
          <input type="checkbox" checked={manualSteps}
                 onChange={(e) => onManualSteps(e.target.checked)} /> 逐步确认
        </label>
      </div>

      <div className="chat-body" ref={scroller}>
        {!stream.length ? (
          <div className="chat-hero">
            <h3>导演在跑这条链</h3>
            <p>留言排队，打回才重做</p>
          </div>
        ) : null}
        {stream.map((m) => (
          <div key={m.kind + m.id}
               className={m.kind === 'edit' ? 'msg msg--edit'
                            : (mine.includes(m.id) || m.by ? 'msg msg--me' : 'msg msg--sys')}>
            {m.text}
            <span className="msg-meta">
              {hhmm(m.at)}
              {m.kind === 'message'
                ? (m.delivered_to
                    ? ' · 已送达 ' + (ROLE_ZH[m.delivered_to] || m.delivered_to)
                    : ' · 排队中' + (m.to ? ' → ' + (ROLE_ZH[m.to] || m.to) : ''))
                : ' · 台账'}
            </span>
          </div>
        ))}
        {error ? <div className="msg msg--sys err">{error}</div> : null}
      </div>

      {pending ? (
        <div className="chat-gate">
          <div>
            下一步 <b>{ROLE_ZH[h?.next_role || ''] || h?.next_role || '?'}</b>
            {' · '}刚出 <b>{ROLE_ZH[h?.prev_role || ''] || h?.prev_role || '?'}</b>
          </div>
          {h?.stale_decision ? <div className="err">⚠️ 你的决定已失效，请重新确认</div> : null}
          <div className="field" style={{ marginTop: 10 }}>
            <textarea rows={2} placeholder="理由（可选）" value={note}
                      onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="chat-gate-row">
            <button type="button" className="btn btn--sm btn--primary" disabled={busy}
                    onClick={async () => setMsg(await onDecide('approve', '', note))}>继续</button>
            <select value={gateTarget} onChange={(e) => setGateTarget(e.target.value)}>
              {gateTargets.map((t) => <option key={t} value={t}>{ROLE_ZH[t] || t}</option>)}
            </select>
            <button type="button" className="btn btn--sm" disabled={busy} data-danger="1"
                    title="重跑该角色及其全部下游"
                    onClick={async () => setMsg(await onDecide('redo', gateTarget, note))}>打回</button>
            <button type="button" className="btn btn--sm btn--danger" disabled={busy} data-danger="1"
                    onClick={async () => setMsg(await onDecide('reject', '', note))}>中止</button>
          </div>
        </div>
      ) : null}

      <div className="chat-input">
        <textarea value={text} placeholder="对导演说…"
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send(); }} />
        <div className="chat-input-row">
          <select value={to} onChange={(e) => setTo(e.target.value)} title="定向给某个角色，留空=下一个被派发的">
            <option value="">任意角色</option>
            {(inbox?.targets || []).filter(Boolean).map((t) => (
              <option key={t} value={t}>{ROLE_ZH[t] || t}</option>
            ))}
          </select>
          <select value={target} onChange={(e) => setTarget(e.target.value)}
                  title="重做哪个角色（含它的下游）">
            <option value="">选角色…</option>
            {idleTargets.map((t) => <option key={t} value={t}>{ROLE_ZH[t] || t}</option>)}
          </select>
          {/* ⛔ 必须人**先选一个角色**才允许按下 —— 不给默认值。
              `data-danger` 是给无头验收脚本看的：它见到这个属性就拒绝点击。 */}
          <button type="button" className="btn btn--sm" disabled={busy || !target} data-danger="1"
                  title="回退该角色及其全部下游并重跑 —— 整表重派实测约 95 分钟"
                  onClick={async () => setMsg(await onRedo(target, text.trim() || note.trim()))}>
            让导演重做
          </button>
          <span className="stage-spacer" />
          <button type="button" className="btn btn--sm btn--primary" disabled={busy || !text.trim()}
                  title="Ctrl / ⌘ + Enter" onClick={() => void send()}>发送</button>
        </div>
        {msg ? <div className="inspector-log err">{msg}</div> : null}
      </div>
    </aside>
  );
}
