/* ==========================================================================
   src/components/Shell.tsx —— 应用外壳（左侧导航 + 顶部栏 + 边缘把手）
   --------------------------------------------------------------------------
   旧版这里有一段**绕**的逻辑，值得单独记一笔，因为它是「换 React 到底买到什么」
   的一个具体答案：

     旧的 `Shell.mountAutoHide()` 要干这些事：
       ① 把手（`.shell-handle`）**手工 appendChild 到 `body`** ——
          因为 `App.paint()` 每次 `#app.innerHTML = ...`，挂在 #app 里会被抹掉；
       ② 用 **`MutationObserver` 监听 `#app`**，在每次重渲染后重新贴 `is-open` 类名
          —— 否则"鼠标没动，面板却莫名收起"；
       ③ 记一堆 `st.sider / st.top` 的中间状态，再手工 `classList.toggle`。

     现在这三件事**全部消失**：
       ① 把手就在 React 树里，重渲染不会抹掉它（React 只更新差异）；
       ② 状态就是 `useState`，类名由 `className={open ? ' is-open' : ''}` 得出 ——
          **不存在"忘了重新贴类名"这个 bug 类别**；
       ③ 唯一保留的命令式部分是"把状态同步到 `document.body` 的类名"，
          因为 `app.css` 的规则是按 `body.is-autohide` 写的（样式不复用改不动它）。
          但它是**单向、幂等**的 effect，不再是"重渲染后补救"。
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Icon, NAV_ITEMS } from './Icons';
import type { NavKey } from './Icons';
import { Router } from '../router';
import { Api } from '../api';

/* ------------------------------------------------------------- 边缘自动隐藏 */

const HOT = 10;      // 边缘热区厚度（px）
const KEEP = 28;     // 悬停保持区向外的宽容度（px）

function useAutoHide() {
  const [siderOpen, setSiderOpen] = useState(false);
  const [topOpen, setTopOpen] = useState(false);
  const siderRef = useRef<HTMLElement | null>(null);
  const topRef = useRef<HTMLElement | null>(null);

  const apply = useCallback((s: boolean, t: boolean) => {
    document.body.classList.add('is-autohide');
    document.body.classList.toggle('shell-hot-left', s);
    document.body.classList.toggle('shell-hot-top', t);
  }, []);

  useEffect(() => {
    if (String(document.body.dataset.shellAutohide || '') === '0') return;

    const near = (el: HTMLElement | null, x: number, y: number, pad: number) => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad;
    };

    // 用 ref 的当前值判断，避免把监听器反复重绑（旧版是靠现查 DOM）
    let s = false;
    let t = false;
    const onMove = (e: MouseEvent) => {
      const x = e.clientX;
      const y = e.clientY;
      const wantS = x <= HOT || (s && near(siderRef.current, x, y, KEEP));
      const wantT = y <= HOT || (t && near(topRef.current, x, y, KEEP));
      if (wantS !== s || wantT !== t) {
        s = wantS;
        t = wantT;
        setSiderOpen(wantS);
        setTopOpen(wantT);
        apply(wantS, wantT);
      }
    };
    // 指针离开整个窗口（mousemove 不再触发）→ 收纳，免得停在展开态
    const onLeave = () => {
      if (s || t) { s = false; t = false; setSiderOpen(false); setTopOpen(false); apply(false, false); }
    };
    // 键盘可达：Tab 进侧栏也展开
    const onFocusIn = (e: FocusEvent) => {
      if (siderRef.current && siderRef.current.contains(e.target as Node) && !s) {
        s = true;
        setSiderOpen(true);
        apply(true, t);
      }
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseleave', onLeave);
    document.addEventListener('focusin', onFocusIn);
    apply(false, false);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseleave', onLeave);
      document.removeEventListener('focusin', onFocusIn);
    };
  }, [apply]);

  return { siderOpen, topOpen, siderRef, topRef };
}

/* ------------------------------------------------------------------ 子部件 */

function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <span style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 8, background: '#121212',
      color: '#fff', fontWeight: 700, fontSize: Math.round(size * 0.58), lineHeight: 1,
    }}>P</span>
  );
}

function Sider({ activeKey, open, innerRef }: {
  activeKey: NavKey | ''; open: boolean; innerRef: React.RefObject<HTMLElement | null>;
}) {
  const iconOf: Record<string, () => ReactNode> = {
    inspiration: () => Icon.inspiration(28),
    playlet: () => Icon.storyboard(28),
    canvas: () => Icon.canvas(28),
    works: () => Icon.project(28),
    assets: () => Icon.assets(28),
  };
  return (
    <aside className={'sider' + (open ? ' is-open' : '')} ref={innerRef as React.RefObject<HTMLElement>}>
      <div className="sider-top">
        <button type="button" id="dle_pavo_logo" className="sider-logo" title="Pavo Home">
          <LogoMark size={36} />
        </button>
        <div className="sider-nav">
          {NAV_ITEMS.map((n) => (
            <button
              key={n.key}
              type="button"
              id={n.domId}
              className={'nav-item' + (n.key === activeKey ? ' is-active' : '')}
              title={n.label}
              data-route={n.route}
              onClick={() => Router.go(n.route)}
            >
              <span className="nav-ico">{iconOf[n.key]()}</span>
              <span className="nav-label">{n.label}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="sider-bottom">
        <span className="nav-item" id="dle_mobile_logo" title="移动端">{Icon.wechat(28)}</span>
        <button type="button" id="dle_settings_logo" className="nav-item" title="设置（未移植）" disabled>
          <span className="nav-ico">{Icon.settings(28)}</span>
        </button>
      </div>
    </aside>
  );
}

/**
 * 顶部栏右侧的算力 / 头像。
 *
 * ⚠️ **与旧版有意不同的一处**：旧版从出厂种子读 `user.credits` 并一直显示 200，
 *    而 http 驱动下**没有任何接口去更新它** —— 也就是一个会悄悄说谎的数字。
 *    这里在拿不到真实数据时显示 `—` 并在 title 里说明，**不假装有值**。
 */
function CreditsBlock() {
  const seed = (window as unknown as { PAVO_SEED?: { user?: { name?: string; avatar?: string; credits?: number } } }).PAVO_SEED;
  const u = seed && seed.user;
  const named = !!(u && u.name);
  return (
    <div className="topbar-right">
      <div className="credits" title={u ? '算力点（出厂快照，尚未接后端）' : '算力点（尚未接后端）'}>
        <span style={{ width: 26, height: 26, borderRadius: '50%', background: '#e9c86a', display: 'inline-block' }} />
        <b style={{ fontWeight: 600 }}>{u && typeof u.credits === 'number' ? u.credits : '—'}</b>
        <span className="sep" />
        <span className="upgrade">升级</span>
      </div>
      <button type="button" className="avatar" aria-label="Account" title={named ? String(u?.name) : '账号（未接后端）'}>
        {named && u?.avatar
          ? <img src={u.avatar} alt="avatar" />
          : (
            <span style={{
              display: 'flex', width: '100%', height: '100%', alignItems: 'center',
              justifyContent: 'center', background: '#0ABCCF', color: '#fff', fontWeight: 600,
            }}>{named ? String(u?.name).slice(0, 1).toUpperCase() : '·'}</span>
          )}
      </button>
    </div>
  );
}

/** 后端连通性横幅 —— 只在**真的连不上**时出现，且说清是「联系不上」还是「后端答复了错误」。 */
function BackendBanner() {
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    Api.ping().then((r) => {
      if (!alive) return;
      // local 驱动 = 离线模式，不是故障
      if (r.driver === 'local') { setState(null); return; }
      if (r.ok) { setState(null); return; }
      setState({ ok: false, text: r.error || '后端未响应' });
    });
    return () => { alive = false; };
  }, []);

  if (!state) return null;
  return (
    <div className="content" style={{ paddingBottom: 0 }}>
      <div className="ov-text" style={{
        background: 'var(--danger-soft, #fdecec)', color: 'var(--danger)',
        border: '1px solid var(--danger)', borderRadius: 8, padding: '10px 14px', fontSize: 13,
      }}>
        后端未连通：{state.text}（当前地址 {Api.baseUrl || '同源'}）—— 列表与生成都会失败；
        请确认 `python -m v5.server --port 8787` 已启动。
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 外壳本体 */

export function Shell({ activeKey, children }: { activeKey: NavKey | ''; children: ReactNode }) {
  const { siderOpen, topOpen, siderRef, topRef } = useAutoHide();
  return (
    <>
      <Sider activeKey={activeKey} open={siderOpen} innerRef={siderRef} />
      <section className="main">
        <header className={'topbar with-sider' + (topOpen ? ' is-open' : '')} ref={topRef as React.RefObject<HTMLElement>}>
          <div className="topbar-center" />
          <CreditsBlock />
        </header>
        <BackendBanner />
        {children}
      </section>
      <div className={'shell-handle shell-handle--left' + (siderOpen ? ' is-hidden' : '')} aria-hidden="true" />
      <div className={'shell-handle shell-handle--top' + (topOpen ? ' is-hidden' : '')} aria-hidden="true" />
    </>
  );
}

/* ---------------------------------------------------------------- 工作台外壳 */

export interface StepInfo {
  key: string;
  /** `1.` / `2.` 之类的序号（已完成时换成对勾图标） */
  no: number;
  label: string;
  state: 'done' | 'active' | 'todo';
}

/**
 * 工作台（生产页）外壳：**没有左侧导航**，顶栏**常驻**（`topbar--pinned`）。
 *
 * 两处都必须照旧版来，理由是实测出来的：
 *  ① 生产页**没有 `.sider`** —— 旧 `mountAutoHide` 专门处理"没有侧栏时把左侧把手藏掉"
 *     （否则屏幕左边挂着一条点了没反应的死控件）。
 *  ② 顶栏常驻：用户 2026-09-18 要求「进入生产页面后 header 应该固定、不要自动隐藏」
 *     —— 这一页是持续看内容/翻长页的地方，顶栏收起会让人每次都要把鼠标甩到屏幕顶端。
 *
 * ⚠️ **必须同时给 body 打 `shell-top-pinned`**：从列表页过来时
 *    `body.is-autohide` 已经在了（那条 effect 的清理不会去掉它），而
 *    `body.is-autohide .topbar { transform: translateY(-100%) }` 会把顶栏整个推出去。
 *    CSS 里唯一的解药就是 `body.shell-top-pinned` + `.topbar--pinned`
 *    （`app.css`：`body.is-autohide .topbar--pinned { transform: none }`）。
 */
export function WorkbenchShell({ title, steps, scope = 'wizard', onStep, tools, children }: {
  title: string;
  steps: StepInfo[];
  scope?: string;
  onStep?: (key: string) => void;
  /** 常驻顶栏右侧的运行期开关（跨三步都可见 —— 链从第 1 步就会启动，
   *  放在某一步的内容区里等于"链跑起来了才让你设"）。 */
  tools?: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    document.body.classList.add('shell-top-pinned');
    return () => document.body.classList.remove('shell-top-pinned');
  }, []);

  // 与旧版一致：步骤之间插一个 `step-sep`（用 map + join 的等价写法）
  const stepEls: ReactNode[] = [];
  steps.forEach((s, i) => {
    if (i > 0) stepEls.push(<span className="step-sep" key={'sep-' + s.key}>{Icon.chevronRight(16)}</span>);
    stepEls.push(
      <button
        key={s.key}
        type="button"
        className={'step' + (s.state === 'active' ? ' is-active' : '')
          + (s.state === 'done' ? ' is-done' : '') + (s.state !== 'active' ? ' is-clickable' : '')}
        data-step={s.key}
        aria-current={s.state === 'active' ? 'step' : undefined}
        onClick={() => { if (s.state !== 'active') onStep?.(s.key); }}
      >
        {s.state === 'done'
          ? <span className="step-ico">{Icon.check(20)}</span>
          : <span className="step-num">{s.no}.</span>}
        <span>{s.label}</span>
      </button>,
    );
  });

  return (
    <>
      <header className="topbar topbar--pinned">
        <button type="button" className="back-btn" aria-label="返回" onClick={() => Router.go('/playlet/list')}>
          {Icon.back(24)}
        </button>
        <span className="topbar-title">{title}</span>
        <div className="topbar-center">
          {steps.length
            ? <div className="stepper" data-stepper={scope}>{stepEls}</div>
            : null}
        </div>
        {tools ? <span className="topbar-tools">{tools}</span> : null}
        <CreditsBlock />
      </header>
      <section className="main" style={{ marginLeft: 0 }}>{children}</section>
    </>
  );
}
