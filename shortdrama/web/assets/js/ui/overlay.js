/* ==========================================================================
   ui/overlay.js —— 浮层原语
   复刻线上实际使用的四类浮层（均由实测 DOM 归纳）：
     Modal   ：粘贴剧本 / 新剧集 / 重命名 —— 居中卡片 + 取消/确认
     Drawer  ：角色信息（名称·声音·描述·单图/四视图生成·历史）—— 右侧全屏面板
     Menu    ：剧本操作 / 项目卡 / 资产 / 剧集 ··· —— 锚点定位下拉
     Popover ：关键帧/视频「历史记录」—— 锚点气泡
   共同行为：Esc 关闭、点击遮罩关闭、点击外部关闭、焦点管理、层级互斥。
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D;

  let zBase = 1500;
  const stack = [];

  function esc(e) { if (e.key === 'Escape') { const top = stack[stack.length - 1]; if (top) { e.stopPropagation(); top.close(); } } }

  function push(item) { stack.push(item); if (stack.length === 1) document.addEventListener('keydown', esc, true); }
  function pop(item) {
    const i = stack.indexOf(item); if (i >= 0) stack.splice(i, 1);
    if (!stack.length) document.removeEventListener('keydown', esc, true);
    if (item.el && item.el.parentElement) item.el.parentElement.removeChild(item.el);
  }

  /* ------------------------------------------------------------------ Modal */
  /**
   * @param {{title:string, body:string, width?:number, confirmText?:string,
   *          cancelText?:string, danger?:boolean, hint?:string,
   *          onConfirm?:Function, onClose?:Function, onReady?:Function}} o
   */
  function modal(o) {
    const wrap = D.el('div', { class: 'ov-mask' });
    wrap.style.zIndex = ++zBase;
    wrap.innerHTML =
      '<div class="ov-modal" style="width:' + (o.width || 480) + 'px" role="dialog" aria-modal="true">' +
      '<div class="ov-modal-head"><h3>' + D.esc(o.title || '') + '</h3>' +
      '<button type="button" class="ov-close" data-ov="close" aria-label="关闭">✕</button></div>' +
      '<div class="ov-modal-body">' + (o.body || '') + '</div>' +
      (o.hint ? '<div class="ov-modal-hint">' + D.esc(o.hint) + '</div>' : '') +
      '<div class="ov-modal-foot">' +
      '<button type="button" class="btn btn--sm" data-ov="close">' + D.esc(o.cancelText || '取消') + '</button>' +
      '<button type="button" class="btn btn--sm ' + (o.danger ? 'btn--danger' : 'btn--primary') + '" data-ov="ok"' +
      (o.confirmDisabled ? ' disabled' : '') + '>' + D.esc(o.confirmText || '完成') + '</button>' +
      '</div></div>';
    document.body.appendChild(wrap);

    const self = { el: wrap, close: function () { pop(self); if (o.onClose) o.onClose(); } };
    push(self);

    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) self.close(); });
    wrap.addEventListener('click', function (e) {
      const b = e.target.closest('[data-ov]'); if (!b) return;
      const k = b.getAttribute('data-ov');
      if (k === 'close') return self.close();
      if (k === 'ok') { if (o.onConfirm && o.onConfirm(wrap, self) === false) return; self.close(); }
    });

    const api = {
      el: wrap,
      close: self.close,
      root: wrap,
      setDisabled: function (v) { const b = wrap.querySelector('[data-ov="ok"]'); if (b) b.disabled = !!v; },
      setCount: function (sel, cur, max) { const t = wrap.querySelector(sel); if (t) t.textContent = cur + '/' + max; },
    };
    // 自动焦点 + 计数器联动
    const first = wrap.querySelector('input,textarea'); if (first) setTimeout(function () { first.focus(); }, 30);
    if (o.onReady) o.onReady(api);
    return api;
  }

  /* ----------------------------------------------------------------- Drawer */
  /** 角色信息面板：右侧全屏抽屉 */
  function drawer(o) {
    const wrap = D.el('div', { class: 'ov-drawer-mask' });
    wrap.style.zIndex = ++zBase;
    wrap.innerHTML = '<div class="ov-drawer" role="dialog" aria-modal="true">' + (o.body || '') + '</div>';
    document.body.appendChild(wrap);
    const self = { el: wrap, close: function () { pop(self); if (o.onClose) o.onClose(); } };
    push(self);
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) self.close(); });
    wrap.addEventListener('click', function (e) {
      const b = e.target.closest('[data-ov="close"]'); if (b) self.close();
    });
    const api = { el: wrap, root: wrap, close: self.close };
    if (o.onReady) o.onReady(api);
    return api;
  }

  /* ------------------------------------------------------------------- Menu */
  /** 锚点下拉菜单：items = [{label, action, danger, divider}] */
  function menu(anchor, items, opts) {
    closeMenus();
    const el = D.el('div', { class: 'ov-menu' });
    el.style.zIndex = ++zBase;
    el.innerHTML = items.map(function (it, i) {
      if (it.divider) return '<div class="ov-menu-div"></div>';
      return '<div class="ov-menu-item' + (it.danger ? ' danger' : '') + '" data-mi="' + i + '">' +
        D.esc(it.label) + (it.right ? '<span class="ov-menu-right">' + D.esc(it.right) + '</span>' : '') + '</div>';
    }).join('');
    document.body.appendChild(el);

    const r = anchor.getBoundingClientRect();
    const o = opts || {};
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = o.align === 'right' ? r.right - w : r.left;
    let top = r.bottom + 8;
    if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 8);
    if (left + w > innerWidth - 8) left = innerWidth - w - 8;
    if (left < 8) left = 8;
    el.style.left = left + 'px';
    el.style.top = top + 'px';

    const self = { el: el, close: function () { pop(self); document.removeEventListener('mousedown', onOut, true); } };
    push(self);
    function onOut(e) { if (!el.contains(e.target) && !anchor.contains(e.target)) self.close(); }
    setTimeout(function () { document.addEventListener('mousedown', onOut, true); }, 0);

    el.addEventListener('click', function (e) {
      const it = e.target.closest('[data-mi]'); if (!it) return;
      const item = items[Number(it.getAttribute('data-mi'))];
      self.close();
      if (item && item.action) setTimeout(function () { item.action(); }, 0);
    });
    return self;
  }
  function closeMenus() { D.$$('.ov-menu').forEach(function (e) { e.remove(); }); }

  /* ---------------------------------------------------------------- Popover */
  function popover(anchor, html, opts) {
    closeMenus();
    const el = D.el('div', { class: 'ov-popover' });
    el.style.zIndex = ++zBase;
    el.innerHTML = html;
    if ((opts || {}).wide) el.style.width = '320px';
    document.body.appendChild(el);
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = Math.max(8, r.right - w), top = r.bottom + 8;
    if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 8);
    el.style.left = left + 'px'; el.style.top = top + 'px';
    const self = { el: el, close: function () { pop(self); document.removeEventListener('mousedown', onOut, true); } };
    push(self);
    function onOut(e) { if (!el.contains(e.target) && !anchor.contains(e.target)) self.close(); }
    setTimeout(function () { document.addEventListener('mousedown', onOut, true); }, 0);
    return self;
  }

  /* ---------------------------------------------------------------- Confirm */
  function confirm(o) {
    return modal({
      title: o.title || '确认操作',
      body: '<div class="ov-text">' + D.esc(o.text || '') + '</div>',
      width: 420, danger: o.danger, confirmText: o.confirmText || (o.danger ? '删除' : '确认'),
      onConfirm: function () { if (o.onOk) o.onOk(); },
    });
  }

  /** 输入类弹窗：带字数计数与「空则禁用确认」 */
  function prompt(o) {
    const max = o.max || 40;
    const single = o.multiline !== true;
    const input = single
      ? '<input class="ov-input" data-ovinput maxlength="' + max + '" placeholder="' + D.esc(o.placeholder || '') + '" value="' + D.esc(o.value || '') + '">'
      : '<textarea class="ov-textarea" data-ovinput maxlength="' + max + '" placeholder="' + D.esc(o.placeholder || '') + '">' + D.esc(o.value || '') + '</textarea>';
    return modal({
      title: o.title,
      width: o.multiline ? 560 : 480,
      hint: o.hint,
      body: input +
        '<div class="ov-count"><span data-ovcount>0</span>/' + max + '</div>',
      confirmText: o.confirmText || '确认',
      onConfirm: function (root, self) {
        const v = (root.querySelector('[data-ovinput]').value || '').trim();
        if (!v) return false;
        if (o.onConfirm) o.onConfirm(v);
        return true;
      },
      onReady: function (api) {
        const inp = api.root.querySelector('[data-ovinput]');
        const cnt = api.root.querySelector('[data-ovcount]');
        const sync = function () { cnt.textContent = inp.value.length; api.setDisabled(!inp.value.trim()); };
        inp.addEventListener('input', sync); sync();
        setTimeout(function () { inp.focus(); }, 30);
      },
    });
  }

  global.Overlay = { modal, drawer, menu, popover, confirm, prompt, closeMenus };
})(window);
