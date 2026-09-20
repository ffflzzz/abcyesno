/* ==========================================================================
   src/App.tsx —— 路由 → 水合 → 渲染
   --------------------------------------------------------------------------
   结构上对齐旧 `app.js` 的 `render()` / `paint()` 两级，只是搬进了 React：

     旧：render() { 水合；然后 paint() }        paint() { 视图函数 → innerHTML }
     新：useEffect 水合；然后 JSX 按路由出组件

   两条纪律原样保留：
     ① ★★ **水合失败绝不阻断渲染** —— 回落本地缓存 + 弹提示。
        否则后端一挂整个界面白屏，连"哪里错了"都看不见（本项目「失败必须可见」）。
     ② ★★ **报错要说对话** —— 「后端答复了 404」与「根本联系不上」是两件事，
        一律显示"后端未连通"会把排查方向带偏（旧版 2026-09-17 实测踩到）。
   ========================================================================== */

import { useEffect, useRef } from 'react';
import { hydrate } from './api';
import type { RouteInfo } from './types';
import { Router, useRoute } from './router';
import { Api } from './api';
import { Store, useStore } from './store';
import { Shell } from './components/Shell';
import type { NavKey } from './components/Icons';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toasts } from './components/Toasts';
import { PlayletList } from './pages/PlayletList';
import { Wizard } from './pages/Wizard';
import { Storyboard } from './pages/Storyboard';
import { Canvas, Inspiration, NotFound, Visuals, Works } from './pages/Placeholders';
import { GenOverlayHost } from './components/GenOverlay';
import { HitlBarHost } from './components/HitlBar';

function activeNavKey(route: RouteInfo): NavKey | '' {
  if (route.path.startsWith('/playlet')) return 'playlet';
  if (route.path === '/inspiration' || route.path === '/') return 'inspiration';
  if (route.path === '/canvas') return 'canvas';
  if (route.path === '/chat') return 'chat' as NavKey;
  if (route.path === '/visuals') return 'assets';
  return '';
}

export function App() {
  const route = useRoute();
  const { hydrate: hy } = useStore();

  // 并发保护：连续切路由时，只有最新那次允许写状态（旧 app.js 的 `renderSeq`）
  const seqRef = useRef(0);
  const routeKey = route.path + '|' + JSON.stringify(route.params);

  useEffect(() => {
    // 离线模式（local 驱动）用出厂种子数据 —— 直接引用旧 web/ 的那份，不复制。
    if (Api.driver !== 'http') {
      Store.loadSeed();
      Store.setHydrate({ hydrated: false, from: 'local' });
      return;
    }
    const seq = ++seqRef.current;
    /**
     * DEV 埋点：记下**每次水合是谁触发的**（路由键 + hash）。
     *
     * 为什么值得常驻一段调试代码：`useEffect` 的依赖是派生的 `routeKey`，
     * 一旦它意外变化（比如拿到新对象引用、或某个上游状态把它带变了），
     * 表现就是"多打了一轮请求"—— 而**光看网络日志分不出是哪一次渲染触发的**。
     * 这里落一个计数在窗口上，`__APPERR` 的同类做法（见 ErrorBoundary）。
     */
    if (import.meta.env.DEV) {
      // ⚠️ 存 **sessionStorage** 而不是 window：整页导航会重建 window，
      //   埋点会被清空 ⇒ "水合几次"就没法和"导航几次"对齐了（第一版就栽在这）。
      //   sessionStorage 在同一个标签页内跨导航存活，正好用来数这件事。
      try {
        const raw = sessionStorage.getItem('__hydrations');
        const list = raw ? (JSON.parse(raw) as unknown[]) : [];
        list.push({ key: routeKey, hash: location.hash, at: Date.now() });
        sessionStorage.setItem('__hydrations', JSON.stringify(list.slice(-100)));
      } catch { /* 隐私模式等情况下拿不到 storage，忽略即可 */ }
    }
    hydrate(route, {
      upsertProject: (p) => Store.upsertProject(p),
      upsertProjects: (l, replace) => Store.upsertProjects(l, replace),
      upsertStoryboard: (pid, eid, sb) => Store.upsertStoryboard(pid, eid, sb),
      setStyles: (s) => Store.setStyles(s),
      setVendors: (v) => Store.setVendors(v),
    })
      .then((r) => { if (seq === seqRef.current) Store.setHydrate(r); })
      .catch((err: Error & { isNetwork?: boolean; status?: number }) => {
        // ★ 两类失败说两句不同的话（见文件头 ②）
        let msg: string;
        if (err.isNetwork) msg = '后端未连通，显示本地缓存：' + err.message;
        else if (err.status) msg = '后端拒绝了这次请求（HTTP ' + err.status + '）：' + err.message;
        else msg = '水合失败，显示本地缓存：' + err.message;
        if (seq === seqRef.current) {
          Store.setHydrate({ hydrated: false, error: msg });
          Store.toast(msg, 'error');
        }
      });
    // 依赖 routeKey 而不是 route 对象：路由对象每次 hashchange 都是新引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey]);

  return (
    <>
      {isWorkbench(route) ? (
        <ErrorBoundary key={routeKey} onReset={() => Router.go(route.path)}>
          {renderPage(route)}
        </ErrorBoundary>
      ) : (
        <Shell activeKey={activeNavKey(route)}>
          <ErrorBoundary key={routeKey} onReset={() => Router.go(route.path)}>
            {renderPage(route)}
          </ErrorBoundary>
          {hy && !hy.hydrated && hy.from && hy.from !== 'local' ? (
            <div className="content" style={{ paddingTop: 0 }}>
              <div className="muted small">水合未完成（{hy.from}）：界面显示的是缓存/出厂数据。</div>
            </div>
          ) : null}
        </Shell>
      )}
      <Toasts />
      {/*
        这两个是**常驻层**，刻意挂在最外层（与页面平级）：
          · GenOverlayHost —— 全屏过场；状态在 `lib/genOverlay.ts`，
            任何页面都能 `GenOverlay.show()`，而渲染点不会被页面重渲染带走。
          · HitlBarHost    —— 步级人工确认条；必须**跨页存活**（人在等的时候
            很可能翻到别的页面，挂在页面里就"翻页即消失 ⇒ 死等"）。
      */}
      <GenOverlayHost />
      <HitlBarHost pid={currentPid(route)} />
    </>
  );
}

function renderPage(route: RouteInfo) {
  if (route.path === '/playlet/list' || route.path === '/') return <PlayletList />;

  // 分镜编辑器：与向导页一样是**工作台页**（自带顶栏、无侧栏）
  if (route.pattern === '/playlet/review/:pid/episode/:eid') {
    return <Storyboard pid={route.params.pid} eid={route.params.eid} />;
  }
  if (route.pattern === '/playlet/review/:pid') {
    return <Wizard pid={route.params.pid} step={route.query.step} />;
  }

  // 一级导航的其余页面
  if (route.path === '/inspiration') return <Inspiration />;
  if (route.path === '/canvas') return <Canvas />;
  if (route.path === '/chat') return <Works />;
  if (route.path === '/visuals') return <Visuals />;
  return <NotFound path={route.path} />;
}

/** 工作台页自己出顶栏（且没有侧栏）⇒ App 不能再包一层带侧栏的外壳。 */
function isWorkbench(route: RouteInfo): boolean {
  return route.pattern === '/playlet/review/:pid'
    || route.pattern === '/playlet/review/:pid/episode/:eid';
}

/** 当前路由对应的项目 id（人工确认条要按它轮询）。列表页没有 ⇒ null。 */
function currentPid(route: RouteInfo): string | null {
  const pid = route.params?.pid;
  const bad = !pid || pid === 'null' || pid === 'undefined';
  return bad ? null : pid;
}
