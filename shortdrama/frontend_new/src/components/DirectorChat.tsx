/* ==========================================================================
   components/DirectorChat.tsx —— 右栏：与 supervisor / director 对话
   --------------------------------------------------------------------------
   ★ 两条通道，**按链此刻的状态自动选**（这是设计要点，不是兜底）：
     · 链**正挂在步级门上**（`hitl.pending`）→ 「继续 / 打回 / 中止」按钮，
       走 `hitl.decide`，**立刻生效**，能带自由文字理由；
     · 链**在跑或没跑** → 信箱，落盘排队，**下一个被派发的角色**读到它
       （投递语义见 `v5/inbox.py`）。
   ⛔ 界面不许说"已发送给导演"而实际只是排进队列 —— 所以每条自己的消息都
      显示真实状态：排队中 / 已送达某角色。`delivered_to` 是盘上事实，不是猜的。
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { InboxState } from '../lib/studio';
import { ROLE_ZH, hhmm } from '../lib/studio';

type Props = {
  inbox: InboxState | null;
  error: string;
  busy: boolean;
  /** 返回 `{err, id}`：`id` 是后端给的信箱编号，宿主用它把这条认成「我说的」。
   *  ⛔ 不能靠 `by` 判断 —— 那是人填的显示名，同一条消息换个标签就跑到对面去了。 */
  onSend: (text: string, to: string) => Promise<{ err: string | null; id?: number }>;
  mine: number[];
  onDecide: (decision: string, target: string, note: string) => Promise<string | null>;
  onRedo: (target: string, note: string) => Promise<string | null>;
};

export function DirectorChat({ inbox, error, busy, onSend, onDecide, onRedo, mine }: Props) {
  const [text, setText] = useState('');
  const [to, setTo] = useState('');
  const [note, setNote] = useState('');
  const [target, setTarget] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);

  const h = inbox?.hitl;
  const pending = !!h?.pending;
  const targets = useMemo(() => (h?.redo_targets && h.redo_targets.length
    ? h.redo_targets : (inbox?.targets || []).filter(Boolean)), [h, inbox]);

  useEffect(() => { if (!target && targets.length) setTarget(String(targets[0])); }, [targets, target]);

  const stream = useMemo(() => {
    const items = [...(inbox?.messages || []), ...(inbox?.edits || [])]
      .sort((a, b) => a.id - b.id);
    return items;
  }, [inbox]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [stream.length]);

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
        <span className="stage-spacer" />
        <span className="chat-state">
          {pending ? '链已暂停，等你确认'
            : inbox?.manual_steps ? '逐步确认已开，下一步会停'
            : inbox ? '链在推进，留言排队到下一个派发口' : ''}
        </span>
      </div>

      <div className="chat-body" ref={scroller}>
        {!stream.length ? (
          <div className="msg msg--sys">
            还没有留言。{'\n'}这里说的话不会改已生成的产物 —— 要重做请点下面的「打回重做」。
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
                    : ' · 排队中' + (m.to ? '（定向 ' + (ROLE_ZH[m.to] || m.to) + '）' : ''))
                : ' · 自动记账'}
            </span>
          </div>
        ))}
        {error ? <div className="msg msg--sys err">{error}</div> : null}
      </div>

      {pending ? (
        <div className="chat-gate">
          <div>
            下一步要派发 <b>{ROLE_ZH[h?.next_role || ''] || h?.next_role || '?'}</b>
            {' · '}刚产出的是 <b>{ROLE_ZH[h?.prev_role || ''] || h?.prev_role || '?'}</b>
          </div>
          {h?.stale_decision ? <div className="err">⚠️ 盘上有个已失效的决定（戳不匹配），请重新确认。</div> : null}
          <div className="field" style={{ marginTop: 8 }}>
            <textarea rows={2} placeholder="（可选）理由 —— 打回时会原样交给导演"
                      value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="chat-gate-row">
            <button type="button" className="btn btn--sm btn--primary" disabled={busy}
                    onClick={async () => setMsg(await onDecide('approve', '', note))}>继续</button>
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              {targets.map((t) => <option key={t} value={t}>{ROLE_ZH[t] || t}</option>)}
            </select>
            <button type="button" className="btn btn--sm" disabled={busy}
                    onClick={async () => setMsg(await onDecide('redo', target, note))}>打回</button>
            <button type="button" className="btn btn--sm btn--danger" disabled={busy}
                    onClick={async () => setMsg(await onDecide('reject', '', note))}>中止</button>
          </div>
        </div>
      ) : null}

      <div className="chat-input">
        <textarea value={text} placeholder="对导演说…（链跑着也能说，会排队到下一个派发口）"
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send();
                  }} />
        <div className="chat-input-row">
          <select value={to} onChange={(e) => setTo(e.target.value)} title="定向给某个角色">
            <option value="">任意角色</option>
            {(inbox?.targets || []).filter(Boolean).map((t) => (
              <option key={t} value={t}>{ROLE_ZH[t] || t}</option>
            ))}
          </select>
          <button type="button" className="btn btn--sm" disabled={busy || !target}
                  title="回退该角色及其全部下游并重跑 —— 整表重派实测约 95 分钟"
                  onClick={async () => setMsg(await onRedo(target, text.trim() || note.trim()))}>
            让导演重做
          </button>
          <span className="stage-spacer" />
          <button type="button" className="btn btn--sm btn--primary" disabled={busy || !text.trim()}
                  onClick={() => void send()}>发送</button>
        </div>
        {msg ? <div className="inspector-log err">{msg}</div> : null}
        <div className="inspector-hint">
          Ctrl/⌘+Enter 发送 · 信箱不自动回退重跑，要重做点「让导演重做」
        </div>
      </div>
    </aside>
  );
}
