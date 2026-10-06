/* ==========================================================================
   src/components/GenOverlay.tsx —— 全屏过场动画（渲染层）
   状态在 `lib/genOverlay.ts`；这里只负责画，并且**挂在最外层**
   （App 里渲染一次），因此不会被任何页面的重渲染带走。

   层级关系照旧版：`.gen-overlay` 1200 < `.hitl-bar` 1400 < `.toast-wrap` 2000。
   ========================================================================== */

import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useGenOverlay } from '../lib/genOverlay';

function LogoMark({ size = 56 }: { size?: number }) {
  return (
    <span style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 10, background: '#121212',
      color: '#fff', fontWeight: 700, fontSize: Math.round(size * 0.58), lineHeight: 1,
    }}>P</span>
  );
}

export function GenOverlayHost() {
  const st = useGenOverlay();

  useEffect(() => {
    document.body.classList.toggle('has-gen-overlay', !!st);
    return () => document.body.classList.remove('has-gen-overlay');
  }, [st]);

  if (!st) return null;
  return createPortal(
    <div className="gen-overlay">
      <div className="gen-ov-card">
        <div className="gen-ov-logo"><LogoMark size={56} /></div>
        <div className="gen-ov-title">{st.title}</div>
        <div className="gen-ov-sub">{st.sub || ''}</div>
        {st.onStop ? (
          <button type="button" className="btn btn--sm gen-ov-stop" onClick={st.onStop}>停止</button>
        ) : null}
        <div className="gen-ov-spin" aria-hidden="true" />
      </div>
    </div>,
    document.body,
  );
}
