/* ==========================================================================
   ui/gen-overlay.js —— 全屏「过场动画」（2026-09-19）
   ==========================================================================
   用途：**等待生成**期间盖住整页，不让用户看到"还没内容的工作台"。
   用户原话：「剧本没有生成出来之前，不允许进入工作台才对，不然用户还看不到内容，
   只看到空白页面」——所以两段生成（简介 / 剧本正文）期间都要有它。

   样式对齐线上（用户给的两张截图）：白底、居中、logo + 标题 + 副标题 + 「停止」。

   两条硬纪律：
     ① **不抢人工确认条的层级**：`z-index: 1200` < `.hitl-bar` 的 1400。
        逐步人工确认开着时链会停在"派发下一个角色之前"等人 —— 那一刻调用方必须
        `hide()`（判据用"确认条在不在场"，见 wizard 的 `genScriptThenConfirm`），
        这里只保证"就算两者同时在场，人也能看见确认条"。
     ② **toast（z-index 2000）要盖在它上面**：等待期间的状态提示不能被吃掉。

   用法：
     GenOverlay.show({title, sub, onStop})   // onStop 非空才渲染「停止」
     GenOverlay.update({sub})                // 只改文案（幂等，不重建节点）
     GenOverlay.hide()
     GenOverlay.visible()
   ========================================================================== */
(function (global) {
  'use strict';

  const ID = 'gen-overlay';
  let cur = null;                 // {title, sub, onStop}

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function node() { return global.document ? global.document.getElementById(ID) : null; }

  function render() {
    const e = node();
    if (!e || !cur) return;
    const logo = (global.Shell && global.Shell.logoMark) ? global.Shell.logoMark(56) : '';
    e.innerHTML =
      '<div class="gen-ov-card">' +
      '<div class="gen-ov-logo">' + logo + '</div>' +
      '<div class="gen-ov-title">' + esc(cur.title) + '</div>' +
      '<div class="gen-ov-sub">' + esc(cur.sub || '') + '</div>' +
      (cur.onStop ? '<button type="button" class="btn btn--sm gen-ov-stop">停止</button>' : '') +
      '<div class="gen-ov-spin" aria-hidden="true"></div>' +
      '</div>';
    e.onclick = function (ev) {
      const t = ev.target;
      if (t && t.closest && t.closest('.gen-ov-stop') && cur && cur.onStop) cur.onStop();
    };
  }

  /**
   * 显示（已在场则只更新文案 —— 幂等，不重建 DOM）。
   *
   * ⚠️ 不重建 DOM 就换不了 `onStop` 闭包 —— 但实际调用点每次都传同一个函数，
   *    所以这里**故意不做**重建逻辑（写了反而会被读成"忘了处理"）。
   */
  function show(o) {
    o = o || {};
    const existed = !!node();
    cur = { title: o.title || '正在生成…', sub: o.sub || '', onStop: o.onStop || null };
    if (!existed) {
      const e = global.document.createElement('div');
      e.id = ID;
      e.className = 'gen-overlay';
      global.document.body.appendChild(e);
      global.document.body.classList.add('has-gen-overlay');
    }
    render();
    return true;
  }

  function update(o) {
    if (!cur) return false;
    cur = Object.assign({}, cur, o || {});
    render();
    return true;
  }

  function hide() {
    const e = node();
    if (e && e.parentNode) e.parentNode.removeChild(e);
    cur = null;
    if (global.document && global.document.body) {
      global.document.body.classList.remove('has-gen-overlay');
    }
    return false;
  }

  function visible() { return !!node(); }

  global.GenOverlay = { show: show, update: update, hide: hide, visible: visible,
                        get data() { return cur; } };
})(window);
