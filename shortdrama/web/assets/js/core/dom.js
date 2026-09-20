/* ==========================================================================
   core/dom.js —— 极简 DOM 工具
   采用「模板字符串 + 事件委托」渲染，避免引入框架，保证 file:// 直开可用。
   ========================================================================== */
(function (global) {
  'use strict';

  /** 转义 HTML，防止用户输入破坏结构 */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** 查询 */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  /** 创建元素 */
  function el(tag, attrs, children) {
    const n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'class') n.className = attrs[k];
        else if (k === 'html') n.innerHTML = attrs[k];
        else if (k === 'text') n.textContent = attrs[k];
        else if (k === 'style' && typeof attrs[k] === 'object') Object.assign(n.style, attrs[k]);
        else if (k.slice(0, 2) === 'on' && typeof attrs[k] === 'function') n.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] != null && attrs[k] !== false) n.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return n;
  }

  /** 渲染到容器 */
  function render(container, html) {
    container.innerHTML = html;
    return container;
  }

  /** 事件委托：在根节点上按选择器匹配 */
  function delegate(root, eventName, selector, handler) {
    root.addEventListener(eventName, function (e) {
      const target = e.target.closest(selector);
      if (target && root.contains(target)) handler(e, target);
    });
  }

  /** 格式化时长 ms -> mm:ss */
  function fmtDuration(ms) {
    const total = Math.round((ms || 0) / 1000);
    const m = Math.floor(total / 60), s = total % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  /** 本地 id 生成（保持与线上 id 同样的「长数字串」外观） */
  let idSeed = Date.now();
  function nextId() {
    idSeed += Math.floor(Math.random() * 1000) + 1;
    return String(idSeed) + String(Math.floor(Math.random() * 100000)).padStart(5, '0');
  }

  /** 轻量 HTML 转义后的富文本渲染：把 [{type:'text'|'ref'}] 渲染成 @chip */
  function richHtml(tokens) {
    if (!tokens || !tokens.length) return '';
    return tokens.map(function (t) {
      if (t.type === 'ref') {
        return '<span class="ref-chip" data-ref-kind="' + esc(t.kind) + '" data-ref-id="' + esc(t.id) + '">' +
          '@' + esc(t.name) + '</span>';
      }
      return esc(t.value).replace(/\n/g, '<br>');
    }).join('');
  }

  /** 把纯文本按 @[名称](sd-asset://kind/id) 解析成 token（与线上语法一致） */
  const RE_TOKEN = /@\[([^\]]+)\]\(sd-asset:\/\/([a-zA-Z]+)\/(\d+)\)/g;
  function parseRich(text) {
    const out = []; let last = 0; const s = text || '';
    let m;
    RE_TOKEN.lastIndex = 0;
    while ((m = RE_TOKEN.exec(s)) !== null) {
      if (m.index > last) out.push({ type: 'text', value: s.slice(last, m.index) });
      out.push({ type: 'ref', name: m[1], kind: m[2], id: m[3] });
      last = m.index + m[0].length;
    }
    if (last < s.length) out.push({ type: 'text', value: s.slice(last) });
    return out;
  }

  /** 简易防抖 */
  function debounce(fn, wait) {
    let t;
    return function () {
      const args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, wait || 300);
    };
  }

  global.D = { esc, $, $$, el, render, delegate, fmtDuration, nextId, richHtml, parseRich, debounce };
})(window);
