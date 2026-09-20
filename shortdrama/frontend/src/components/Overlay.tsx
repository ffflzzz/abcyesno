/* ==========================================================================
   src/components/Overlay.tsx —— 弹层（模态 / 输入 / 确认）
   --------------------------------------------------------------------------
   旧版这些弹层是**命令式**的：`document.body.appendChild(mask)` + 手工 addEventListener
   + 手工 `remove()`。改成 React 后就是**声明式 + portal**：

     · 「挂 body」这件事由 `createPortal` 表达 —— 语义上仍然挂在 body
       （这是刻意的：弹层不能被视图重渲染带走），但**不需要谁来管它的生死**；
     · 不再有 `mask.remove()`、`onReady(api)`、`root.querySelector(...)`
       这一整套"渲染完再去 DOM 里找元素"的回调。
   类名（`.ov-*`）与旧版一致 ⇒ 直接复用 `web/assets/css/app.css`，样式不分叉。
   ========================================================================== */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

function Mask({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div
      className="ov-mask"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {children}
    </div>
  );
}

export interface ModalProps {
  title: string;
  body: ReactNode;
  hint?: string;
  width?: number;
  danger?: boolean;
  confirmText?: string;
  cancelText?: string;
  confirmDisabled?: boolean;
  onConfirm?: () => void;
  onClose: () => void;
}

export function Modal(o: ModalProps) {
  return createPortal(
    <Mask onClose={o.onClose}>
      <div className="ov-modal" style={{ width: o.width || 480 }} role="dialog" aria-modal="true">
        <div className="ov-modal-head">
          <h3>{o.title}</h3>
          <button type="button" className="ov-close" aria-label="关闭" onClick={o.onClose}>✕</button>
        </div>
        <div className="ov-modal-body">{o.body}</div>
        {o.hint ? <div className="ov-modal-hint">{o.hint}</div> : null}
        <div className="ov-modal-foot">
          <button type="button" className="btn btn--sm" onClick={o.onClose}>
            {o.cancelText || '取消'}
          </button>
          <button
            type="button"
            className={'btn btn--sm ' + (o.danger ? 'btn--danger' : 'btn--primary')}
            disabled={o.confirmDisabled}
            onClick={() => { if (o.onConfirm) o.onConfirm(); }}
          >
            {o.confirmText || '确定'}
          </button>
        </div>
      </div>
    </Mask>,
    document.body,
  );
}

/** 带字数上限的输入弹窗（项目重命名 / 粘贴剧本都用它）。 */
export function PromptModal(o: {
  title: string;
  value?: string;
  max?: number;
  placeholder?: string;
  textarea?: boolean;
  hint?: string;
  confirmText?: string;
  onConfirm: (v: string) => void;
  onClose: () => void;
}) {
  const max = o.max || 40;
  const [v, setV] = useState(o.value || '');
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  useEffect(() => { ref.current?.focus(); }, []);

  const countStyle = { textAlign: 'right' as const };
  return (
    <Modal
      title={o.title}
      width={o.textarea ? 640 : 480}
      hint={o.hint}
      confirmText={o.confirmText}
      confirmDisabled={!v.trim()}
      onClose={o.onClose}
      onConfirm={() => { if (v.trim()) o.onConfirm(v.trim()); }}
      body={
        <>
          {o.textarea ? (
            <textarea
              ref={ref as React.RefObject<HTMLTextAreaElement>}
              className="ov-textarea" style={{ minHeight: 260 }} maxLength={max}
              placeholder={o.placeholder} value={v}
              onChange={(e) => setV(e.target.value)}
            />
          ) : (
            <input
              ref={ref as React.RefObject<HTMLInputElement>}
              className="ov-input" maxLength={max}
              placeholder={o.placeholder} value={v}
              onChange={(e) => setV(e.target.value)}
            />
          )}
          <div className="ov-count" style={countStyle}><span>{v.length}</span>/{max}</div>
        </>
      }
    />
  );
}

/** 确认弹窗（删除项目用它，红色）。 */
export function ConfirmModal(o: {
  title: string;
  text: string;
  danger?: boolean;
  confirmText?: string;
  onOk: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      title={o.title}
      width={440}
      danger={o.danger}
      confirmText={o.confirmText}
      onClose={o.onClose}
      onConfirm={o.onOk}
      body={<div className="ov-text">{o.text}</div>}
    />
  );
}

/* -------------------------------------------------------------------- 抽屉 */

/**
 * 全屏抽屉（资产信息面板用它）。类名与旧版 `Overlay.drawer` 一致
 * （`.ov-mask` > `.ov-drawer`），所以 `app.css` 那套 `ad-*` 样式直接生效。
 */
export function Drawer({ body, onClose, width }: {
  body: ReactNode; onClose: () => void; width?: number;
}) {
  return createPortal(
    <Mask onClose={onClose}>
      <div className="ov-drawer" role="dialog" aria-modal="true"
        style={width ? { width } : undefined}>
        {body}
      </div>
    </Mask>,
    document.body,
  );
}

/* -------------------------------------------------------------------- 菜单 */

export interface MenuItem {
  label: string;
  danger?: boolean;
  divider?: boolean;
  action?: () => void;
}

/**
 * 锚点下拉菜单（门户 + `position: fixed`）。
 *
 * 为什么**必须**是门户、不能像卡片那样内联在容器里（2026-09-19 真浏览器量到）：
 *   内联版把菜单挂在 `.project-card` 内部，菜单底部实测到 **916px**，而视口高 **905px**
 *   —— **最后一项落在视口外，点不到**。旧版 `Overlay.menu` 就带"超出底部就往上翻"
 *   的逻辑（`if (top + h > innerHeight - 8) top = r.top - h - 8`），
 *   我第一版直接内联、把这套丢了 ⇒ 等于引入了一个"元素存在但不可点"的回归。
 *
 * 这里把旧版那套几何**原样搬过来**（含左右的夹取），因为它的判据是量出来的。
 */
export function Menu(o: {
  anchor: DOMRect;
  anchorEl?: HTMLElement | null;
  items: MenuItem[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  // 先量后放：`useLayoutEffect` 在绘制前跑，避免"先出现在左上角再跳过去"
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = o.anchor;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let left = r.right - w;                      // 与旧版一致：卡片菜单右对齐
    let top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
    if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
    if (left < 8) left = 8;
    setPos({ left, top });
  }, [o.anchor]);

  useEffect(() => {
    // 捕获阶段监听 mousedown，并延后一拍注册 —— 否则"打开菜单的那一次点击"会立刻把它关掉
    const handle = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current && ref.current.contains(t)) return;
      if (o.anchorEl && o.anchorEl.contains(t)) return;
      o.onClose();
    };
    const id = window.setTimeout(() => document.addEventListener('mousedown', handle, true), 0);
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') o.onClose(); };
    document.addEventListener('keydown', onKey);
    return () => {
      window.clearTimeout(id);
      document.removeEventListener('mousedown', handle, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [o.onClose, o.anchorEl]);

  return createPortal(
    <div
      ref={ref}
      className="ov-menu"
      role="menu"
      style={{
        left: pos ? pos.left : 0,
        top: pos ? pos.top : 0,
        visibility: pos ? 'visible' : 'hidden',
      }}
    >
      {o.items.map((it, i) => (
        it.divider
          ? <div className="ov-menu-div" key={'d' + i} />
          : (
            <div
              key={it.label}
              className={'ov-menu-item' + (it.danger ? ' danger' : '')}
              role="menuitem"
              onClick={() => { o.onClose(); it.action?.(); }}
            >{it.label}</div>
          )
      ))}
    </div>,
    document.body,
  );
}
