/* ==========================================================================
   src/components/Toasts.tsx —— 提示条
   旧版是 `Store.toast()` 里手工 createElement + appendChild 到 `body`；
   这里是声明式的：状态在 store 里，渲染由 React 负责，**不需要谁去 remove 节点**。
   ========================================================================== */

import { createPortal } from 'react-dom';
import { useStore } from '../store';

export function Toasts() {
  const { toasts } = useStore();
  if (!toasts.length) return null;
  return createPortal(
    <div className="toast-wrap" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={'toast' + (t.kind === 'error' ? ' is-error' : '')}>
          {t.kind === 'ok' ? <span className="ok">✓</span> : null}
          {t.text}
        </div>
      ))}
    </div>,
    document.body,
  );
}
