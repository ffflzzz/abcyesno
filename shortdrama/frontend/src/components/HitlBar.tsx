/* ==========================================================================
   src/components/HitlBar.tsx —— 步级「逐步人工确认」的常驻确认条
   --------------------------------------------------------------------------
   链路（`scripts/drive_chain.py`）每派一个角色**之前**会挂起，等人给决定。
   人在等的时候很可能翻到别的页面去了 —— 所以这条**挂在 `<body>` 上**（portal），
   不随页面重渲染消失。旧版那条注释记的就是这个坑（"常驻 UI 挂 body，别挂 #app"）：
   挂在页面里的话，你一翻页按钮就没了，而任务还在等 —— **死等**。

   两个按钮（**刻意只有两个**）：
     · 继续 → `POST {decision:'approve'}` → 链路 resume，跑 `next_role`
     · 打回 → 收一句原因 → `POST {decision:'redo', target, note}`
              → 链路把该角色**及其下游**的旧产物移入 `.rerun_backup/` 后重跑

   为什么不给「中止」按钮：`reject` 是运维动作（要走 CLI 的 `--hitl-reject`）。
   把"中止整条链"摆在前端，和"重做这一步"只差一次误点 —— 而代价是整条链没了。

   ★★ 两处按旧版**踩过的坑**刻意处理（都写在代码里）：
     ① **轮询 404 必须停手**：项目被删/移走后，5 秒轮询会**永久刷 404 且完全静默**
        （只有看控制台才发现）。认 `err.status === 404` 后停掉并说一句。
     ② **后台标签页定时器会被节流** ⇒ 补 `visibilitychange`：一旦可见**立刻**查一次，
        否则"该消失的条一直挂着 / 该出现的迟迟不出现"会被误读成代码坏了。
   ========================================================================== */

import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Api } from '../api';
import { Store } from '../store';
import type { ApiError, HitlState } from '../types';
import { PromptModal } from './Overlay';

/** 角色 → 中文产物名（给用户看的；后端只认角色名） */
const ROLE_CN: Record<string, string> = {
  // `director` 不是被派发的角色，但它有一份产物（`director.md` = 制作规格：
  // 片长/画幅/音频模式/视觉基准），而且是**第一停**时人唯一能审的东西。
  director: '制作规格',
  worldbuilder: '世界观与角色卡',
  assetdesigner: '资产卡',
  plotdesigner: '剧情大纲',
  scriptwriter: '剧本',
  dialogue: '台词清单',
  scenedesigner: '分镜表',
  reviewer: '审片报告',
};

export function roleLabel(role?: string): string {
  if (!role) return '—';
  return ROLE_CN[role] ? ROLE_CN[role] + '（' + role + '）' : role;
}

const POLL_MS = 5000;

export function HitlBarHost({ pid }: { pid: string | null }) {
  const [st, setSt] = useState<HitlState | null>(null);
  const [busyText, setBusyText] = useState('');
  const [redo, setRedo] = useState(false);

  const send = useCallback(async (body: Record<string, unknown>) => {
    if (!pid) return;
    setBusyText(body.decision === 'redo' ? '正在打回…' : '正在继续…');
    try {
      const next = await Api.postHitl(pid, body) as HitlState;
      setBusyText('');
      Store.toast(body.decision === 'redo'
        ? '已打回：' + roleLabel(String(body.target)) + ' 及其下游将重跑'
        : '已继续：链路接着往下跑', 'ok');
      // 用返回的最新状态重画（pending 变 false 就自动收起）
      setSt(next || { pending: false });
    } catch (e) {
      setBusyText('');
      Store.toast('提交失败：' + (e as Error).message, 'error');
      // 失败要**把按钮放回来**，否则用户就卡在一个点不动的条上
    }
  }, [pid]);

  useEffect(() => {
    setSt(null);
    if (!pid) return;
    let alive = true;
    let stopped = false;
    let timer = 0;

    const tick = async () => {
      if (stopped || !alive) return;
      try {
        const s = await Api.getHitl(pid);
        if (alive) setSt(s);
      } catch (e) {
        const err = e as ApiError;
        // ★ ① 404 = 项目没了 ⇒ **停手**，别永久刷（旧版实测到的静默 404 循环）
        if (err.status === 404) {
          stopped = true;
          if (alive) setSt(null);
          Store.toast('项目已不存在，停止查询人工确认状态');
          return;
        }
        // 其它错误（网络抖动 / 后端重启）⇒ 继续轮询，不打扰用户
      }
      if (alive && !stopped) timer = window.setTimeout(tick, POLL_MS);
    };
    tick();

    // ★ ② 后台标签页会被 Chrome 强节流 ⇒ 一旦可见立刻补一次
    const onVis = () => {
      if (document.visibilityState === 'visible' && !stopped) {
        window.clearTimeout(timer);
        tick();
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      alive = false;
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [pid]);

  /* 预留底部空间：只切 body 类名，**不量高度**。
     ⚠️ 为什么不能量（旧版实测，很重要）：设 padding → 文档变长 → 出现纵向滚动条
     → `100vw` 变窄 → 条子换行方式变 → 高度从 145 变 163 → 预留量又变 ⇒ **反馈环**。
     所以按实测两档在 CSS 里写死预留（桌面 112px / 窄屏 220px）。 */
  const pending = !!(st && st.pending);
  useEffect(() => {
    document.body.classList.toggle('has-hitl-bar', pending);
    return () => document.body.classList.remove('has-hitl-bar');
  }, [pending]);

  if (!pending || !st) return null;

  const prev = roleLabel(st.prev_role);
  const next = roleLabel(st.next_role);
  const targets = st.redo_targets || [];
  const def = st.prev_role && targets.includes(st.prev_role) ? st.prev_role : targets[targets.length - 1];

  return createPortal(
    <>
      <div className={'hitl-bar' + (busyText ? ' is-busy' : '')} role="status">
        <span className="hitl-dot" aria-hidden="true" />
        <div className="hitl-text">
          <b>等你确认</b>
          <span>刚产出：<b>{prev}</b>　→　下一步：<b>{next}</b></span>
          {st.stale_decision ? (
            <span className="hitl-warn">⚠️ 你上一次的点击已失效（那一步已经过去）—— 请重新确认</span>
          ) : null}
          {st.decision ? (
            <span className="hitl-warn">已提交「{String(st.decision)}」，等待链路响应…</span>
          ) : null}
        </div>
        <span className="hitl-busy">{busyText}</span>
        <button
          type="button" className="btn btn--sm" disabled={!!busyText}
          onClick={() => {
            if (!targets.length) {
              Store.toast('没有可打回的目标（还没有任何角色完成）');
              return;
            }
            setRedo(true);
          }}
        >打回重做</button>
        <button
          type="button" className="btn btn--sm btn--primary" disabled={!!busyText}
          onClick={() => send({ decision: 'approve', stamp: st.stamp || '' })}
        >继续</button>
      </div>

      {redo ? (
        <PromptModal
          title="打回重做" textarea max={300} value=""
          placeholder="哪里不满意？（会原样交给导演，越具体越好）"
          hint={'会重跑「' + roleLabel(def) + '」及其下游（上游不动）。'
            + '可打回的角色：' + targets.map(roleLabel).join('、')}
          confirmText="打回重做"
          onClose={() => setRedo(false)}
          onConfirm={(note) => {
            setRedo(false);
            send({ decision: 'redo', target: def, note, stamp: st.stamp || '' });
          }}
        />
      ) : null}
    </>,
    document.body,
  );
}
