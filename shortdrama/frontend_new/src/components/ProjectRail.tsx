/* ==========================================================================
   components/ProjectRail.tsx —— 左栏：并行生产 + 历史项目的会话管理
   --------------------------------------------------------------------------
   分三组，判据全是**盘上事实**（不是前端自己猜的）：
     · 正在生产 —— `GET /runs?pid=` 里有非终态 run
     · 等你确认 —— `hitl.pending`（链挂在步级门上）
     · 历史项目 —— 其余
   ⚠️ 为什么"正在生产"不能看 `progress.status`：那个字段是 `completed|draft`
   （有没有出过片），跟"这一分钟有没有在跑"是两件事。
   ========================================================================== */

import { useMemo, useState } from 'react';
import type { Project } from '../types';
import { ROLE_ZH, hhmm, isRunning } from '../lib/studio';

export type RailSignals = {
  running: Record<string, string>;      // pid → run status
  pending: Record<string, boolean>;     // pid → hitl 挂起
  nextRole: Record<string, string>;     // pid → 下一步派谁
};

type Props = {
  projects: Project[];
  signals: RailSignals;
  pid: string;
  onPick: (pid: string) => void;
  onNew: (topic: string, pack: string, episodes: number, ratio: string) => Promise<void>;
  packs: string[];
  ratios: string[];
  busy: boolean;
  /** 管理动作。「管理并行生产」这句话里"管理"指的就是这三个：改名、停跑、删。 */
  runIds: Record<string, string>;
  onRename: (pid: string, name: string) => Promise<void>;
  onStop: (pid: string) => Promise<void>;
  onDelete: (pid: string) => Promise<void>;
};

export function ProjectRail({ projects, signals, pid, onPick, onNew, packs, ratios, busy,
  runIds, onRename, onStop, onDelete }: Props) {
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);
  const [topic, setTopic] = useState('');
  const [pack, setPack] = useState('shortdrama');
  const [eps, setEps] = useState(1);
  const [ratio, setRatio] = useState('9:16');

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle
      ? projects.filter((p) => (String(p.name || '') + String(p.id || '')).toLowerCase().includes(needle))
      : projects;
  }, [projects, q]);

  const running = shown.filter((p) => isRunning(signals.running[p.id]));
  const waiting = shown.filter((p) => !isRunning(signals.running[p.id]) && signals.pending[p.id]);
  const history = shown.filter((p) => !isRunning(signals.running[p.id]) && !signals.pending[p.id]);

  const row = (p: Project, kind: 'run' | 'wait' | 'idle') => (
    <div key={p.id}>
      <button type="button"
              className={'rail-item' + (p.id === pid ? ' rail-item--on' : '')}
              onClick={() => onPick(p.id)}>
        <span className={'dot' + (kind === 'run' ? ' dot--run' : kind === 'wait' ? ' dot--wait' : '')} />
        <span className="rail-item-main">
          {/* ★ 主标签用 **pid**（目录名），副标签才是中文片名。
              理由与截图一致，且是有用处的：pid 才是接口参数、产物目录名、
              命令行里要敲的那个串；片名（`brief.topic`）可以随便改也会撞名，
              把它放主位会让人照着它去敲 CLI 而失败。 */}
          <span className="rail-item-name">{p.id}</span>
          <span className="rail-item-sub">
            {kind === 'run' ? '正在生产'
              : kind === 'wait' ? '等你确认 · ' + (ROLE_ZH[signals.nextRole[p.id] || ''] || signals.nextRole[p.id] || '')
              : ([p.name, p.style?.name].filter(Boolean).join(' · ') || '（无片名）')
                + (p.created_at ? ' · ' + hhmm(p.created_at) : '')}
          </span>
        </span>
      </button>
      {p.id === pid ? (
        <div style={{ display: 'flex', gap: 6, padding: '0 10px 6px 26px', flexWrap: 'wrap' }}>
          <button type="button" className="btn btn--sm" disabled={busy}
                  onClick={() => {
                    const v = window.prompt('改片名（写进 brief.topic，目录名不动）', p.name || p.id);
                    if (v && v.trim()) void onRename(p.id, v.trim());
                  }}>改名</button>
          {kind === 'run' ? (
            <button type="button" className="btn btn--sm btn--danger" disabled={busy || !runIds[p.id]}
                    onClick={() => void onStop(p.id)}>停掉这次跑</button>
          ) : null}
          <button type="button" className="btn btn--sm btn--danger" disabled={busy}
                  onClick={() => void onDelete(p.id)}>删除</button>
        </div>
      ) : null}
    </div>
  );

  return (
    <aside className="rail">
      <div className="rail-head">
        <span className="rail-title">项目</span>
        <span className="stage-spacer" />
        <button type="button" className="btn btn--sm btn--primary" disabled={busy}
                onClick={() => setCreating((v) => !v)}>＋ 新建</button>
      </div>

      {creating ? (
        <div style={{ padding: '0 12px 10px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <input placeholder="项目名（英文标识）" value={topic} onChange={(e) => setTopic(e.target.value)} />
          <div className="field--row" style={{ display: 'flex', gap: 6 }}>
            <select value={pack} onChange={(e) => setPack(e.target.value)} style={{ flex: 1 }}>
              {(packs.length ? packs : ['shortdrama']).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={ratio} onChange={(e) => setRatio(e.target.value)}>
              {(ratios.length ? ratios : ['9:16']).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <input type="number" min={1} max={12} value={eps} style={{ width: 54 }}
                   onChange={(e) => setEps(Math.max(1, Number(e.target.value) || 1))} />
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button type="button" className="btn btn--sm btn--primary" disabled={busy || !topic.trim()}
                    onClick={async () => {
                      await onNew(topic.trim(), pack, eps, ratio);
                      setCreating(false); setTopic('');
                    }}>{busy ? <span className="spin" /> : '创建'}</button>
            <button type="button" className="btn btn--sm" onClick={() => setCreating(false)}>取消</button>
          </div>
          <span className="inspector-hint">建项目会调一次 LLM 提炼 brief（约 10–40 秒）。
            建完到中间那栏点「开始生产」。</span>
        </div>
      ) : null}

      <div className="rail-search">
        <input placeholder="搜索项目" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="rail-body">
        {running.length ? <><div className="rail-group"><span>正在生产</span><span>{running.length}</span></div>
          {running.map((p) => row(p, 'run'))}</> : null}
        {waiting.length ? <><div className="rail-group"><span>等你确认</span><span>{waiting.length}</span></div>
          {waiting.map((p) => row(p, 'wait'))}</> : null}
        <div className="rail-group"><span>历史项目</span><span>{history.length}</span></div>
        {history.map((p) => row(p, 'idle'))}
        {!shown.length ? <div className="inspector-hint" style={{ padding: 10 }}>没有匹配的项目</div> : null}
      </div>

      <div className="rail-foot">
        <a className="btn btn--sm" href="./" target="_blank" rel="noreferrer">旧工作台</a>
        <span className="stage-spacer" />
        <span className="chat-state">{projects.length} 个</span>
      </div>
    </aside>
  );
}
