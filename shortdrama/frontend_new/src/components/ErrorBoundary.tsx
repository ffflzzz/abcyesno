/* ==========================================================================
   src/components/ErrorBoundary.tsx —— 渲染失败必须**可见**
   --------------------------------------------------------------------------
   这条是旧版用事故换来的判据（`app.js:paint()` 的 try/catch）：

     原先异常会穿透渲染入口，DOM 保持上一次的内容 ⇒ 现象是"点了路由没反应 /
     页面没换"，而真正的错因完全看不到。旧版实测：分镜页崩了，界面却仍显示向导页。

   React 默认的失败形态**恰好就是那个坏形态**：组件抛错 → 整棵树被卸载 →
   白屏或残留上一次画面。⇒ 必须显式加边界，并且把错因**印在屏幕上**。
   `window.__APPERR` 沿用旧版约定，便于用 CDP 一条命令取到全部错误。
   ========================================================================== */

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

declare global {
  interface Window { __APPERR?: string[] }
}

interface Props { children: ReactNode; onReset?: () => void }
interface State { stack: string | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { stack: null };

  static getDerivedStateFromError(err: unknown): State {
    return { stack: String((err as Error)?.stack || (err as Error)?.message || err) };
  }

  componentDidCatch(err: unknown, info: ErrorInfo): void {
    if (!window.__APPERR) window.__APPERR = [];
    window.__APPERR.push('render: ' + ((err as Error)?.message || String(err)));
    // 控制台也留一份 —— 无浏览器断言 / CDP 都靠它
    console.error('[render] 页面渲染失败', err, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.stack === null) return this.props.children;
    return (
      <div style={{ padding: 32 }}>
        <h2 style={{ fontSize: 15, margin: '0 0 8px' }}>页面渲染失败</h2>
        <pre style={{
          whiteSpace: 'pre-wrap', color: 'var(--danger)', fontSize: 12, margin: '0 0 12px',
        }}>{this.state.stack}</pre>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => { this.setState({ stack: null }); this.props.onReset?.(); }}
        >重试渲染</button>
      </div>
    );
  }
}
