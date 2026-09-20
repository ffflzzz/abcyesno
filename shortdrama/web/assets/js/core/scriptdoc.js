/* ==========================================================================
   core/scriptdoc.js —— 剧本正文 → 结构化 HTML（只读展示用）
   --------------------------------------------------------------------------
   为什么需要它：`scriptwriter` 的产物是 markdown，直接丢进 <textarea> 给人看，
   界面上就是 `> 系列：…`、`**场景：**`、`#### 镜头 3（近景，7s）` 这种源码 ——
   2026-09-18 用户原话：「可读性很差，页面 html 混乱」。

   ★ **零依赖**（自带转义，不取 global.D）—— 老式 <script> 是顺序依赖，
     零依赖 ⇒ 在 index.html 里的位置**不影响正确性**（少一类坑）。

   ★ 两条解析纪律（本项目踩出来的，见 docs 里"解析模型产物"那套）：
     1. **先转义再拼 HTML**：模型产物里出现 `<`、`&` 是常事，绝不能直接 innerHTML。
     2. **兜底必须能兜住一切**：认不出的行按普通段落输出，**绝不丢内容**；
        格式全变了也只是"排得糙"，不会"东西没了"。

   ★ 输入有**两大类**，都要伺候（实测各自都有）：
     · AI 生成（idea 模式）：`# 第N集：…` + `> 元信息` + `## 场次列表` +
       `### 第N场` / `#### 镜头 N（景别，Ns）` + `画面：…` + `说话人（语气）` + 台词
       ⚠️ 标题层级**各项目不一致**（有的用 ### 当镜头、有的用 ####）⇒ 按"数量"分层不可靠，
          必须按**标题文字**判断（含「镜头」→ 镜头行；含「场」→ 场次行）。
     · 用户粘贴（script 模式）：**纯文本**，形如 `第一场内景 老屋 - 日` + 动作行，零 markdown。
   ========================================================================== */
(function (global) {
  'use strict';

  const RE_FENCE = /^`{3,}/;                       // 代码围栏：纯语法噪声，无内容
  const RE_HEAD = /^(#{1,6})\s*(.*)$/;
  const RE_HR = /^[-*_]{3,}$/;
  const RE_QUOTE = /^>\s?(.*)$/;
  const RE_BOLD_KV = /^\*\*(.+?)\*\*\s*[：:]?\s*(.*)$/;   // **场景：** 圣托马斯教堂
  const RE_KV = /^([^：:]{1,16})[：:]\s*(.*)$/;           // 画面：… / 场景：…
  const RE_CN_NUM = '一二三四五六七八九十零百千\\d';
  // 场次行：markdown 的 `### 第N场…`、中文编号的 `第三场`、粘贴型 `第一场内景 老屋 - 日`、`1.内景 便利店 - 夜`
  // ⚠️ **必须锚在"场次"这个形态上**，不能宽泛到"标题里含『场』" ——
  //    实测 `## 场次列表` 会被那种写法误判成场次行（本次自测当场抓到）。
  const RE_SCENE = new RegExp(
    '^(?:第\\s*[' + RE_CN_NUM + ']+\\s*[场幕]' +
    '|场\\s*[' + RE_CN_NUM + ']+' +
    '|\\d+\\s*[.、]\\s*(?:内景|外景|日|夜|晨|黄昏))');
  const RE_SHOT = /^镜头\s*\d+/;                    // 镜头 3（近景，7s）
  const RE_NOTE = /^[（(].*[)）]$/;                 // （无对白，环境音：…）
  const RE_BULLET = /^[-*+]\s+(.*)$/;
  // ★★ 尖括号在模型产物里**不是 HTML**，有两种用法（2026-09-18 真实产物全量扫出 91 处）：
  //   ① 独立短标签，标出**下一行是什么内容**：`<画面>`(51) `<动作描述>`(18)
  //      `<场景描述>`(11) `<动作>`(9) `<环境音>`(1) `<定格>`(1)
  //   ② 把**整行**包起来当"这是描述"的强调：`<石砌拱顶压得很低…泛着冷光。>`
  // ⚠️ 不处理的话：①会被当成**说话人**、紧接着的描述会被标成**台词**（语义全错），
  //    ②会原样显示成 `&lt;…&gt;` —— 用户看到的正是"页面 html 混乱"。
  const RE_TAG = /^<\s*([^<>]{1,12})\s*>$/;
  // ⚠️ 内层**不许再出现尖括号**：否则 `<script>alert(1)</script>` 这种行会被
  //    当成"包裹"而吞掉首尾两个括号（内容被改写，且看起来像渲染出错）。
  //    实测语句：模型包裹的是一句中文描述，里面从不含 `<>`。
  const RE_WRAP = /^<([^<>]{6,})>$/;
  // 说话人：`巴赫（严肃、不紧不慢）` —— 语气括注可缺
  const RE_SPEAKER = /^([^：:（）()]{1,14})\s*[（(]([^）)]{0,40})[)）]\s*$/;
  // 句末标点：有它就不是"说话人那一行"
  const RE_SENT_END = /[。！？…；;.!?，,、]/;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** 行内 `**粗体**`：**在已转义的文本上**替换，所以插入的标签是安全的。 */
  function inline(s) {
    return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  /**
   * 剧本正文 → 块数组。每个块 `{ t: 类型, ... }`：
   *   h     标题（`lvl` 1-6，`role` = 'scene' | 'shot' | 'plain'）
   *   scene 场次行            shot 镜头行
   *   kv    标签 + 值          action 画面/动作段落
   *   speaker 说话人           line 台词
   *   note  括注              li 列表项
   *   meta  元信息行（连续多条会被渲染器并成一块）
   *   hr / p                普通段落
   */
  function parse(md) {
    const out = [];
    const lines = String(md == null ? '' : md).replace(/\r\n?/g, '\n').split('\n');
    let afterTag = false;                 // 上一行是 `<画面>` 这类标签 ⇒ 本行是描述不是台词
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i].replace(/\s+$/, '');
      const s = raw.trim();
      if (!s) { out.push({ t: 'blank' }); continue; }
      const wasTag = afterTag; afterTag = false;
      if (RE_FENCE.test(s)) continue;                       // 围栏噪声
      if (RE_HR.test(s)) { out.push({ t: 'hr' }); continue; }

      let m;
      if ((m = RE_QUOTE.exec(s))) { out.push({ t: 'meta', text: m[1] }); continue; }
      // 短标签先判（它比"整行包裹"更具体）
      if ((m = RE_TAG.exec(s)) && !RE_SENT_END.test(m[1])) {
        afterTag = true;
        out.push({ t: 'tag', text: m[1] });
        continue;
      }
      if ((m = RE_WRAP.exec(s))) { out.push({ t: 'p', text: m[1] }); continue; }
      if ((m = RE_HEAD.exec(s))) {
        const text = m[2].trim();
        const lvl = m[1].length;
        // ★ 角色按**标题文字**判，不按层级 —— 层级各项目不一致（见文件头）
        const role = RE_SHOT.test(text) ? 'shot' : (RE_SCENE.test(text) ? 'scene' : 'plain');
        out.push({ t: 'h', lvl: lvl, role: role, text: text });
        continue;
      }
      if ((m = RE_BOLD_KV.exec(s))) { out.push({ t: 'kv', k: m[1].trim(), v: m[2].trim() }); continue; }
      if (RE_SCENE.test(s)) { out.push({ t: 'scene', text: s }); continue; }
      if (RE_SHOT.test(s)) { out.push({ t: 'shot', text: s }); continue; }
      if (RE_NOTE.test(s) && s.length <= 60) { out.push({ t: 'note', text: s }); continue; }
      if ((m = RE_BULLET.exec(s))) { out.push({ t: 'li', text: m[1].trim() }); continue; }
      if ((m = RE_KV.exec(s)) && m[1].length <= 8) {
        // `画面：…` 是动作段落；其余短标签按 kv
        out.push(m[1] === '画面' || m[1] === '动作'
          ? { t: 'action', text: m[2].trim() }
          : { t: 'kv', k: m[1].trim(), v: m[2].trim() });
        continue;
      }
      if ((m = RE_SPEAKER.exec(s))) { out.push({ t: 'speaker', name: m[1].trim(), tone: m[2].trim() }); continue; }
      // ★ `<画面>` 之后的那一行是**描述**，不是台词 —— 这条必须先于说话人启发式判
      if (wasTag) { out.push({ t: 'action', text: s }); continue; }
      // 无句末标点、且够短 ⇒ 当作说话人（粘贴型剧本常见），下一行是台词
      const prev = out[out.length - 1];
      if (prev && prev.t === 'speaker') { out.push({ t: 'line', text: s }); continue; }
      if (s.length <= 12 && !RE_SENT_END.test(s) && !/^[#>*|]/.test(s)) {
        out.push({ t: 'speaker', name: s, tone: '' }); continue;
      }
      out.push({ t: 'p', text: s });
    }
    return out;
  }

  /** 块数组 → HTML。`meta` 与 `li` 连续出现时并成一块。 */
  function toHtml(blocks) {
    const parts = [];
    let meta = [], lis = [], action = [];

    function flushMeta() {
      if (!meta.length) return;
      parts.push('<div class="sd-meta">' +
        meta.map(function (x) { return '<div class="sd-meta-row">' + inline(x) + '</div>'; }).join('') +
        '</div>');
      meta = [];
    }
    function flushLis() {
      if (!lis.length) return;
      parts.push('<ul class="sd-ul">' +
        lis.map(function (x) { return '<li>' + inline(x) + '</li>'; }).join('') + '</ul>');
      lis = [];
    }
    function flushAction() {
      if (!action.length) return;
      parts.push('<p class="sd-action">' + inline(action.join('')) + '</p>');
      action = [];
    }
    function flushAll() { flushMeta(); flushLis(); flushAction(); }

    blocks.forEach(function (b) {
      switch (b.t) {
        case 'meta': flushLis(); flushAction(); meta.push(b.text); return;
        case 'li': flushMeta(); flushAction(); lis.push(b.text); return;
        case 'blank': flushAll(); return;
        case 'hr': flushAll(); parts.push('<hr class="sd-hr">'); return;
        default: break;
      }
      flushAll();
      switch (b.t) {
        case 'h':
          if (b.role === 'scene') parts.push('<h4 class="sd-scene">' + inline(b.text) + '</h4>');
          else if (b.role === 'shot') parts.push('<h5 class="sd-shot">' + inline(b.text) + '</h5>');
          else parts.push('<h3 class="sd-h sd-h--' + b.lvl + '">' + inline(b.text) + '</h3>');
          return;
        case 'scene': parts.push('<h4 class="sd-scene">' + inline(b.text) + '</h4>'); return;
        case 'shot': parts.push('<h5 class="sd-shot">' + inline(b.text) + '</h5>'); return;
        case 'kv':
          parts.push('<div class="sd-kv"><span class="sd-k">' + inline(b.k) + '</span>' +
            (b.v ? '<span class="sd-v">' + inline(b.v) + '</span>' : '') + '</div>');
          return;
        case 'action': action.push(b.text); return;      // 连续画面行并成一段
        case 'note': parts.push('<div class="sd-note">' + inline(b.text) + '</div>'); return;
        case 'tag': parts.push('<div class="sd-tag">' + inline(b.text) + '</div>'); return;
        case 'speaker':
          parts.push('<div class="sd-speaker">' + inline(b.name) +
            (b.tone ? '<span class="sd-tone">（' + inline(b.tone) + '）</span>' : '') + '</div>');
          return;
        case 'line': parts.push('<p class="sd-line">' + inline(b.text) + '</p>'); return;
        default: parts.push('<p class="sd-p">' + inline(b.text) + '</p>'); return;
      }
    });
    flushAll();
    return parts.join('');
  }

  /** 统计（给测试与排障看：**解析结果要可核对**，不能只报"渲染了"）。 */
  function stats(blocks) {
    const c = { blocks: blocks.length, scenes: 0, shots: 0, lines: 0, speakers: 0, notes: 0, paragraphs: 0 };
    blocks.forEach(function (b) {
      if (b.t === 'blank' || b.t === 'hr') return;
      if (b.t === 'scene' || (b.t === 'h' && b.role === 'scene')) c.scenes++;
      else if (b.t === 'shot' || (b.t === 'h' && b.role === 'shot')) c.shots++;
      else if (b.t === 'line') c.lines++;
      else if (b.t === 'speaker') c.speakers++;
      else if (b.t === 'note') c.notes++;
      else if (b.t === 'p' || b.t === 'action') c.paragraphs++;
    });
    return c;
  }

  /**
   * 对外入口。返回 `{ html, stats }`。
   * ⚠️ **不返回空字符串**：正文非空就一定产出内容（认不出就退化成段落），
   *    "渲染成了空白" 这种情况在结构上不可能发生 —— 这是有意的。
   */
  function render(md) {
    const blocks = parse(md);
    return { html: toHtml(blocks), stats: stats(blocks), blocks: blocks };
  }

  global.ScriptDoc = { render: render, parse: parse, toHtml: toHtml, stats: stats, esc: esc };
})(window);
