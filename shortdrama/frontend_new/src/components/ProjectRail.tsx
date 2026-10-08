/* ==========================================================================
   components/ProjectRail.tsx —— 左栏：并行生产 + 历史项目的会话管理
   --------------------------------------------------------------------------
   分三组，判据全是**盘上事实**：
     · 正在生产 —— `GET /runs?pid=` 里有非终态 run
     · 等你确认 —— `hitl.pending`
     · 历史项目 —— 其余
   ⚠️ "正在生产"不能看 `progress.status`：那是 `completed|draft`（有没有出过片），
   跟"这一分钟有没有在跑"是两件事。

   ★ 每项**只占一行**（pid）。参考图那栏行距 33–36px，我们第一版两行字 = 46–48px，
     一屏少看四分之一的项目。片名挪到副位：只在选中项上出现。
   ========================================================================== */

import { useMemo, useState } from 'react';
import type { Project } from '../types';
import { isRunning } from '../lib/studio';
import { QC_LABELS, qcOn, qcToggle } from '../lib/quality';

export type RailSignals = {
  running: Record<string, string>;
  pending: Record<string, boolean>;
  nextRole: Record<string, string>;
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
  runIds: Record<string, string>;
  onRename: (pid: string, name: string) => Promise<void>;
  onStop: (pid: string) => Promise<void>;
  onDelete: (pid: string) => Promise<void>;
  /** 拖拽手柄（宿主渲染，贴在本栏内侧边缘） */
  splitter?: React.ReactNode;
  onCollapse?: () => void;
};

/** 自动质检开关：常驻项目栏顶（它管的是**接下来那次生成**的行为，值随请求走）。
 *
 * ★ 为什么常驻在这里：这条路径的前提是"人看片"，而"要不要让机器替你判、
 *   并**直接烧配额改画面**"是每次生成前才该决定的事 —— 放页面里、对话框里，
 *   你在看别处时它就不在眼前（2026-10-07 定的纪律：控件先于它治理的动作可见）。
 * ⚠️ 默认全关。静帧那一项在默认档（视频吃资产图、不产静帧）只有回退档才有效。
 */
function QcRow() {
  const [, force] = useState(0);
  return (
    <div className="rail-qc" role="group" aria-label="自动质检"
         style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center',
                  padding: '6px 12px 10px' }}>
      <span style={{ fontSize: 12, opacity: 0.7 }}>自动质检</span>
      {QC_LABELS.map((c) => {
        const on = qcOn(c.key);
        return (
          <button key={c.key} type="button" aria-pressed={on} title={c.hint} data-qc={c.key}
                  onClick={() => { qcToggle(c.key); force((n) => n + 1); }}
                  style={{ fontSize: 12, padding: '2px 10px', borderRadius: 999, cursor: 'pointer',
                           border: '1px solid ' + (on ? '#e9c86a' : 'rgba(255,255,255,.22)'),
                           background: on ? 'rgba(233,200,106,.18)' : 'transparent',
                           color: 'inherit' }}>
            {c.label}
          </button>
        );
      })}
    </div>
  );
}

export function ProjectRail({ projects, signals, pid, onPick, onNew, packs, ratios, busy,
  runIds, onRename, onStop, onDelete, splitter, onCollapse }: Props) {
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
        <span className="rail-item-name">{p.id}</span>
        {p.id === pid && p.name ? <span className="rail-item-sub">{p.name}</span> : null}
      </button>
      {p.id === pid ? (
        <div className="rail-acts">
          <button type="button" className="btn btn--sm" disabled={busy}
                  title="写进 brief.topic；目录名不动"
                  onClick={() => {
                    const v = window.prompt('改片名（目录名不动）', p.name || p.id);
                    if (v && v.trim()) void onRename(p.id, v.trim());
                  }}>改名</button>
          {kind === 'run' ? (
            <button type="button" className="btn btn--sm" disabled={busy || !runIds[p.id]}
                    title={'停掉 ' + runIds[p.id]}
                    onClick={() => void onStop(p.id)} data-danger="1">停跑</button>
          ) : null}
          <button type="button" className="btn btn--sm btn--danger" disabled={busy} data-danger="1"
                  title="移到 .tmp/_deleted/，可恢复"
                  onClick={() => void onDelete(p.id)}>删除</button>
        </div>
      ) : null}
    </div>
  );

  const group = (label: string, list: Project[], kind: 'run' | 'wait' | 'idle') =>
    list.length ? <><div className="rail-group">{label} {list.length}</div>
      {list.map((p) => row(p, kind))}</> : null;

  return (
    <aside className="rail">
      {splitter}
      <div className="rail-head">
        <span className="rail-title">项目</span>
        <span className="stage-spacer" />
        <button type="button" className="btn btn--sm btn--primary" disabled={busy}
                onClick={() => setCreating((v) => !v)}>新建</button>
        {onCollapse ? (
          <button type="button" className="panel-x" title="收起项目栏" onClick={onCollapse}>‹</button>
        ) : null}
      </div>
      <QcRow />

      {creating ? (
        <div style={{ padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input className="rail-new" placeholder="项目名（英文标识）" value={topic}
                 style={inputStyle} onChange={(e) => setTopic(e.target.value)} />
          <div style={{ display: 'flex', gap: 6 }}>
            <select value={pack} style={inputStyle} onChange={(e) => setPack(e.target.value)}>
              {(packs.length ? packs : ['shortdrama']).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={ratio} style={inputStyle} onChange={(e) => setRatio(e.target.value)}>
              {(ratios.length ? ratios : ['9:16']).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <input type="number" min={1} max={12} value={eps} style={{ ...inputStyle, width: 56 }}
                   title="集数" onChange={(e) => setEps(Math.max(1, Number(e.target.value) || 1))} />
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button type="button" className="btn btn--sm btn--primary" disabled={busy || !topic.trim()}
                    onClick={async () => {
                      await onNew(topic.trim(), pack, eps, ratio);
                      setCreating(false); setTopic('');
                    }}>{busy ? <span className="spin" /> : '创建'}</button>
            <button type="button" className="btn btn--sm" onClick={() => setCreating(false)}>取消</button>
          </div>
        </div>
      ) : null}

      <div className="rail-search">
        <input placeholder="搜索项目" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="rail-body">
        {group('正在生产', running, 'run')}
        {group('等你确认', waiting, 'wait')}
        {group('历史项目', history, 'idle')}
        {!shown.length ? <div className="rail-group">没有匹配的项目</div> : null}
      </div>

      <div className="rail-foot">
        <span>{projects.length} 个项目</span>
      </div>
    </aside>
  );
}

const inputStyle: React.CSSProperties = {
  flex: 1, minWidth: 0, padding: '7px 10px', borderRadius: 9, border: 0,
  background: '#17171d', color: 'inherit', font: 'inherit',
};
