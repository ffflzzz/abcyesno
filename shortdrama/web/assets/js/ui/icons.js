/* ==========================================================================
   ui/icons.js —— 内联 SVG 图标
   全部内联，不依赖外部文件，保证离线（含 file://）可用。
   ========================================================================== */
(function (global) {
  'use strict';

  function svg(inner, size, stroke) {
    return '<svg viewBox="0 0 24 24" width="' + (size || 24) + '" height="' + (size || 24) +
      '" fill="none" stroke="currentColor" stroke-width="' + (stroke || 1.6) +
      '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
  }

  const I = {
    inspiration: function (s) { return svg('<path d="M9.5 18h5"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2h5c0-.8.4-1.5 1-2A6 6 0 0 0 12 3Z"/>', s); },
    storyboard: function (s) { return svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/><path d="M3 9h6"/><path d="M3 15h6"/><path d="M13 9h5"/><path d="M13 13h5"/>', s); },
    canvas: function (s) { return svg('<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M9 3v18"/><path d="M3 9h18"/>', s); },
    project: function (s) { return svg('<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3l2 2.5h7A2.5 2.5 0 0 1 20 10v7a2.5 2.5 0 0 1-2.5 2.5h-12A2.5 2.5 0 0 1 3 17Z"/><path d="M12 11.5v5"/><path d="M9.5 14h5"/>', s); },
    assets: function (s) { return svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17.5 9 13l3.5 3 3-2.5L20 17"/>', s); },
    settings: function (s) { return svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.4-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 3a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.4l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11a2 2 0 1 1 0 4Z"/>', s, 1.3); },
    wechat: function (s) { return svg('<path d="M9 4.5c-3.6 0-6.5 2.2-6.5 5 0 1.6.9 3 2.3 4l-.6 2 2.3-1.2c.8.2 1.6.3 2.5.3"/><path d="M21.5 14.6c0-2.3-2.3-4.2-5.2-4.2s-5.2 1.9-5.2 4.2 2.3 4.2 5.2 4.2c.7 0 1.4-.1 2-.3l1.9 1-.5-1.6c1.1-.75 1.8-1.9 1.8-3.3Z"/>', s, 1.5); },
    back: function (s) { return svg('<path d="M15 5 8 12l7 7"/>', s, 1.9); },
    check: function (s) { return '<svg viewBox="0 0 24 24" width="' + (s || 20) + '" height="' + (s || 20) + '" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="#0ABCCF"/><path d="m7.5 12.4 3 3 6-6.4" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'; },
    chevronRight: function (s) { return svg('<path d="m9 6 6 6-6 6"/>', s || 16, 1.8); },
    chevronDown: function (s) { return svg('<path d="m6 9 6 6 6-6"/>', s || 20, 1.8); },
    plus: function (s) { return svg('<path d="M12 5v14"/><path d="M5 12h14"/>', s || 18, 1.9); },
    ellipsis: function (s) { return '<svg viewBox="0 0 24 24" width="' + (s || 20) + '" height="' + (s || 20) + '" aria-hidden="true" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>'; },
    play: function (s) { return '<svg viewBox="0 0 24 24" width="' + (s || 32) + '" height="' + (s || 32) + '" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="rgba(0,0,0,.42)"/><path d="M10 8.2v7.6l6-3.8Z" fill="#fff"/></svg>'; },
    eye: function (s) { return svg('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>', s); },
    edit: function (s) { return svg('<path d="M4 20h4l10-10-4-4L4 16Z"/><path d="m14.5 5.5 4 4"/>', s); },
    trash: function (s) { return svg('<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>', s); },
    refresh: function (s) { return svg('<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v5h-5"/>', s); },
    sync: function (s) { return svg('<path d="M21 12a9 9 0 0 1-15.5 6.2"/><path d="M3 12A9 9 0 0 1 18.5 5.8"/><path d="M3 19v-5h5"/><path d="M21 5v5h-5"/>', s); },
    upload: function (s) { return svg('<path d="M12 16V4"/><path d="m7.5 8.5 4.5-4.5 4.5 4.5"/><path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"/>', s); },
    image: function (s) { return svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17.5 9 13l3.5 3 3-2.5L20 17"/>', s); },
    film: function (s) { return svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 4v16"/><path d="M17 4v16"/><path d="M3 9h4"/><path d="M3 15h4"/><path d="M17 9h4"/><path d="M17 15h4"/>', s); },
    down: function (s) { return svg('<path d="M12 4v12"/><path d="m7.5 11.5 4.5 4.5 4.5-4.5"/><path d="M4 20h16"/>', s); },
    grid: function (s) { return svg('<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>', s); },
    empty: function (s) { return svg('<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 9h18"/><path d="M8 14h8"/>', s || 48, 1.2); },
  };

  /** 导航项定义（与线上 id 对齐，便于对照） */
  const NAV = [
    { key: 'inspiration', label: '灵感', route: '/inspiration', icon: 'inspiration', domId: 'dle_inspiration_logo' },
    { key: 'playlet', label: '短剧', route: '/playlet/list', icon: 'storyboard', domId: 'dle_storyboard_logo' },
    { key: 'canvas', label: '画布', route: '/canvas', icon: 'canvas', domId: 'dle_canvas_logo' },
    { key: 'works', label: '作品', route: '/chat', icon: 'project', domId: 'dle_create_logo' },
    { key: 'assets', label: '资产', route: '/visuals', icon: 'assets', domId: 'dle_assets_logo' },
  ];

  global.Icons = I;
  global.NAV_ITEMS = NAV;
})(window);
