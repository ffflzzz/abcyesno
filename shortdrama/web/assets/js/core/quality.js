/* ==========================================================================
   core/quality.js —— 「自动质检」开关（2026-09-19）
   ==========================================================================
   管的是**质检自愈**那两个环路：
     · 静帧质检（still_qc）—— 判出硬伤就**自动重画**静帧（默认最多 3 轮）
     · 成片复核（clip_qc） —— 抽帧复核不合格就**自动重拍**（默认 2 轮）

   为什么默认**关**（这与"后端默认开"不矛盾，是**路径**不同）：
     这两条环路是"机器替人判断画面合不合格，并**直接烧配额去改**"。
     前端这条路的前提是**判断权在人**（详见 `v5/media/runner.start` 的文档）；
     而 CLI / 外部 agent 走的是另一条路径，那边**没人看片**，所以照旧默认开。
     ⇒ 开关跟着**路径**走，不跟着机器走：值随请求体传给后端，后端只写子进程 env。

   ⛔ 这里**不取 global.D**（自带需要的全部逻辑）—— 想放哪就放哪，顺序无关。
      与本文件同型的还有 `core/scriptdoc.js`。
   ========================================================================== */
(function (global) {
  'use strict';

  const KEY = 'sd.quality.v1';
  //: 默认全关（人工模式）
  const state = { still: false, clip: false };

  (function load() {
    try {
      const s = JSON.parse(global.localStorage.getItem(KEY) || '{}');
      state.still = !!s.still;
      state.clip = !!s.clip;
    } catch (e) { /* 坏数据当默认值用，别让设置页打不开 */ }
  })();

  function save() {
    try { global.localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  }

  /** 开关当前值。`k` ∈ 'still' | 'clip'。 */
  function on(k) { return !!state[k]; }

  /** 翻转一格，返回新值。 */
  function toggle(k) {
    if (!(k in state)) return false;
    state[k] = !state[k];
    save();
    return state[k];
  }

  /**
   * 放进**生成类请求体**的那两个字段。
   *
   * ★ 必须是 `1`/`0` 而不是 `true`/`false`：后端 `runner._qc_flag` 认的是
   *   "数字/字符串 0/1 + 布尔 + 若干假词"，发数字最不容易两边理解不一致。
   */
  function payload() {
    return { still_qc: state.still ? 1 : 0, clip_qc: state.clip ? 1 : 0 };
  }

  function chip(k, label, hint) {
    return '<button type="button" class="qc-chip' + (state[k] ? ' is-on' : '') +
      '" data-qc="' + k + '" aria-pressed="' + (state[k] ? 'true' : 'false') +
      '" title="' + hint + '">' + label + '</button>';
  }

  /** 工具栏里那组小开关（纯 HTML；文案全是常量，不需要转义）。 */
  function chips() {
    return '<span class="qc-switches">' +
      '<span class="qc-label" title="默认全关：判断权在人。打开就是允许机器自己判不合格并重画/重拍">' +
      '自动质检</span>' +
      chip('still', '静帧', '打开：判出硬伤就自动重画静帧（最多 3 轮，烧生图配额）') +
      chip('clip', '成片', '打开：抽帧复核不合格就自动重拍（最多 2 轮，烧视频配额）') +
      '</span>';
  }

  global.Quality = { on: on, toggle: toggle, payload: payload, chips: chips,
                     state: state };
})(window);
