/* ==========================================================================
   core/store.js —— 本地数据层
   目标：以浏览器本地存储替代线上 /api/v1/pixa/short-drama/* 后端，
        数据形状与线上接口保持一致，便于日后替换为真实后端。
   ========================================================================== */
(function (global) {
  'use strict';

  const KEY = 'pavo-offline:v1';
  const D = global.D;

  /** 深拷贝 */
  const clone = (o) => JSON.parse(JSON.stringify(o));

  let state = null;
  const listeners = [];

  function seed() {
    if (!global.PAVO_SEED) throw new Error('缺少种子数据：请确认 assets/js/data/seed.js 已加载');
    const s = clone(global.PAVO_SEED);
    // ★ 风格库：**离线模式也要显示 v5 的真实类型包**。
    //
    // 出厂目录（`seed.js`）是**线上站点 app.pavo-ai.cn 的 20+ 个中文风格名**
    // （`realpeople_horror_film_style` / `anime_3d_style` …），而 v5 的渲染只认
    // 自己的类型包 —— 两套混着用就是「选了 A 实际跑 B」。
    //
    // http 驱动下水合会再覆盖一次（`GET /styles`，**权威**）；这里只是离线快照。
    // `packs.js` 由 `_tools/gen_packs.py` 从 `v5.webmap.styles()` **生成**，
    // 不手写 —— 手写必然走样（v5 当天加第 4 个包时，后端硬编列表就静默拒了它）。
    if (global.PAVO_PACKS && (global.PAVO_PACKS.packs || []).length) {
      s.styles = clone(global.PAVO_PACKS.packs);
    }
    return s;
  }

  /** 风格库是**派生数据**，不进 localStorage。
   *
   * 为什么：它有两个来源（离线快照 `PAVO_PACKS` / 权威 `GET /styles`），
   * 而**缓存里的旧快照会盖掉新快照** —— 用户升级前端后仍看到旧风格列表
   * （实测场景：v5 新增第 4 个包后，缓存里还是 3 个）。
   * 所以每次 `load()` 都从当前来源重新取一遍。
   */
  function freshStyles() {
    const p = global.PAVO_PACKS && global.PAVO_PACKS.packs;
    return (p && p.length) ? clone(p) : null;
  }

  function load() {
    let cached = null;
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.version === seed().version) cached = parsed;
      }
    } catch (e) { console.warn('本地数据读取失败，回退到种子数据', e); }
    if (cached) {
      state = cached;
      const fs_ = freshStyles();
      if (fs_) state.styles = fs_;          // ★ 派生数据每次刷新，不用缓存里的旧值
      state.vendors = null;                 // ★ 厂商档同理：只有后端说了算，缓存不算
      return state;
    }
    state = seed();
    save();
    return state;
  }

  function save() {
    try {
      // 风格库不落盘（见 `freshStyles` 的说明）
      const payload = Object.assign({}, state);
      delete payload.styles;
      localStorage.setItem(KEY, JSON.stringify(payload));
    } catch (e) {
      console.warn('本地数据写入失败（可能超出配额）', e);
      toast('本地存储空间不足，改动可能未保存');
    }
    emit();
  }

  function emit() { listeners.forEach((fn) => { try { fn(state); } catch (e) {} }); }
  function subscribe(fn) { listeners.push(fn); }

  /* ---------- 查询 ---------- */
  const allProjects = () => state.projects;
  const getProject = (pid) => state.projects.find((p) => p.id === pid) || null;
  const getEpisode = (pid, eid) => {
    const p = getProject(pid);
    return p ? (p.episodes.find((e) => e.id === eid) || null) : null;
  };
  function episodeStats(ep) {
    const segs = (ep.storyboard && ep.storyboard.segments) || [];
    const shotCount = segs.reduce((a, s) => a + s.scenes.reduce((b, c) => b + c.shots.length, 0), 0);
    const totalMs = segs.reduce((a, s) => a + (s.duration_ms || 0), 0);
    return {
      segments: segs.length, shots: shotCount, durationMs: totalMs,
      keyframes: segs.filter((s) => s.keyframe).length,
      videos: segs.filter((s) => s.video).length,
    };
  }

  /* ---------- 项目 ---------- */
  function createProject(opts) {
    const o = opts || {};
    const id = D.nextId();
    const epId = D.nextId();
    const now = new Date();
    const stamp = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' +
      String(now.getDate()).padStart(2, '0') + ' ' + String(now.getHours()).padStart(2, '0') + ':' +
      String(now.getMinutes()).padStart(2, '0');
    const styleName = o.style_name || '恐怖电影风格';
    const styleCode = o.style_code || 'realpeople_horror_film_style';
    const project = {
      id: id, name: o.name || '未命名短剧', ratio: o.ratio || '9:16',
      style: { style_id: D.nextId(), code: styleCode, name: styleName, cover_url: '' },
      source_type: o.source_type || 'paste_text', project_type: 'user',
      status: 'draft', asset_locked: false, assets_finalized: false,
      auto_sync_assets: true, readonly: false, planned_episode_count: 1,
      cover: '', created_at: stamp,
      outline: {
        story_type: o.story_type || '未设定',
        target_audience: o.target_audience || '泛人群',
        one_line_story: o.one_line_story || '',
        story_summary: o.story_summary || '',
        world_setting: '', character_biographies: [],
        outline_status: 'draft', editable: true,
      },
      episodes: [{
        id: epId, no: 1, title: o.name || '第 1 集', summary: '',
        script_status: (o.script ? 'completed' : 'draft'),
        script: o.script || '',
        storyboard: { episode_id: epId, episode_no: 1, title: o.name || '', phase: 'draft', ratio: o.ratio || '9:16', segments: [] },
      }],
      assets: { characters: [], scenes: [], props: [] },
      flow: { current_step: 'outline', allowed_actions: [], blocked_actions: [], recommended_actions: [] },
    };
    state.projects.unshift(project);
    save();
    return project;
  }

  function updateProject(pid, patch) {
    const p = getProject(pid); if (!p) return null;
    Object.assign(p, patch); save(); return p;
  }
  function deleteProject(pid) {
    const i = state.projects.findIndex((p) => p.id === pid);
    if (i >= 0) { state.projects.splice(i, 1); save(); }
  }
  function duplicateProject(pid) {
    const p = getProject(pid); if (!p) return null;
    const copy = clone(p);
    copy.id = D.nextId();
    copy.name = p.name + ' 副本';
    copy.episodes.forEach((e) => { e.id = D.nextId(); if (e.storyboard) e.storyboard.episode_id = e.id; });
    state.projects.unshift(copy); save(); return copy;
  }

  /* ---------- 剧集 ---------- */
  function updateScript(pid, eid, text) {
    const ep = getEpisode(pid, eid); if (!ep) return;
    ep.script = text;
    ep.script_status = text.trim() ? 'completed' : 'draft';
    save();
  }
  function renameEpisode(pid, eid, title) {
    const ep = getEpisode(pid, eid); if (!ep) return;
    ep.title = title; if (ep.storyboard) ep.storyboard.title = title; save();
  }
  function addEpisode(pid) {
    const p = getProject(pid); if (!p) return null;
    const id = D.nextId();
    const no = p.episodes.reduce((a, e) => Math.max(a, e.no), 0) + 1;
    const ep = {
      id: id, no: no, title: '第 ' + no + ' 集', summary: '', script_status: 'draft', script: '',
      storyboard: { episode_id: id, episode_no: no, title: '第 ' + no + ' 集', phase: 'draft', ratio: p.ratio, segments: [] },
    };
    p.episodes.push(ep); p.planned_episode_count = p.episodes.length; save(); return ep;
  }
  function deleteEpisode(pid, eid) {
    const p = getProject(pid); if (!p) return;
    const i = p.episodes.findIndex((e) => e.id === eid);
    if (i >= 0) { p.episodes.splice(i, 1); p.planned_episode_count = p.episodes.length; save(); }
  }

  /* ---------- 资产 ---------- */
  function assetBucket(kind) {
    return kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props';
  }
  function addAsset(pid, kind, name) {
    const p = getProject(pid); if (!p) return null;
    const bucket = assetBucket(kind);
    const item = {
      id: D.nextId(), name: name,
      sort_order: p.assets[bucket].length + 1,
      states: [],
    };
    p.assets[bucket].push(item); save(); return item;
  }
  function removeAsset(pid, kind, id) {
    const p = getProject(pid); if (!p) return;
    const bucket = assetBucket(kind);
    p.assets[bucket] = p.assets[bucket].filter((x) => x.id !== id); save();
  }
  function renameAsset(pid, kind, id, name) {
    const p = getProject(pid); if (!p) return;
    const bucket = assetBucket(kind);
    const a = p.assets[bucket].find((x) => x.id === id); if (a) { a.name = name; save(); }
  }
  function addAssetState(pid, kind, id, stateName, image) {
    const p = getProject(pid); if (!p) return null;
    const bucket = assetBucket(kind);
    const a = p.assets[bucket].find((x) => x.id === id); if (!a) return null;
    const refId = D.nextId();
    const st = {
      ref_id: refId, state_name: stateName,
      display_name: a.name + ' - ' + stateName,
      token: '@[' + a.name + ' - ' + stateName + '](sd-asset://' + kind + '/' + refId + ')',
      image: image || '', is_default: a.states.length === 0, asset_type: kind,
    };
    a.states.push(st); save(); return st;
  }
  function removeAssetState(pid, kind, id, refId) {
    const p = getProject(pid); if (!p) return;
    const bucket = assetBucket(kind);
    const a = p.assets[bucket].find((x) => x.id === id); if (!a) return;
    a.states = a.states.filter((s) => s.ref_id !== refId); save();
  }
  function addAssetRef(pid, kind, id, refId, image) {
    const p = getProject(pid); if (!p) return;
    const bucket = assetBucket(kind);
    const a = p.assets[bucket].find((x) => x.id === id); if (!a) return;
    const st = a.states.find((s) => s.ref_id === refId); if (st) { st.image = image; save(); }
  }

  /* ---------- 分镜 ---------- */
  function getSegment(pid, eid, sid) {
    const ep = getEpisode(pid, eid); if (!ep || !ep.storyboard) return null;
    return ep.storyboard.segments.find((s) => s.id === sid) || null;
  }
  function updateSegment(pid, eid, sid, patch) {
    const seg = getSegment(pid, eid, sid); if (!seg) return null;
    Object.assign(seg, patch); save(); return seg;
  }
  function setSegmentMedia(pid, eid, sid, which, value) {
    const seg = getSegment(pid, eid, sid); if (!seg) return null;
    if (which === 'keyframe') seg.keyframe = value; else seg.video = value;
    save(); return seg;
  }
  function addSegment(pid, eid) {
    const ep = getEpisode(pid, eid); if (!ep) return null;
    if (!ep.storyboard) ep.storyboard = { episode_id: eid, episode_no: ep.no, title: ep.title, phase: 'draft', ratio: '9:16', segments: [] };
    const list = ep.storyboard.segments;
    const id = D.nextId();
    const seg = {
      id: id, order: list.length + 1, title: '新镜头 ' + (list.length + 1), summary: '',
      video_prompt: '', duration_ms: 6000, keyframe: '', video: '',
      status: 'draft',
      scenes: [{
        id: D.nextId(), order: 1, title: '新场景', linked_asset_id: '',
        rich: [{ type: 'text', value: '' }], plain: '', refs: [],
        shots: [{
          id: D.nextId(), order: 1, title: '新镜头',
          rich: [{ type: 'text', value: '在此填写画面内容。输入 @ 可引用角色或场景。' }],
          plain: '在此填写画面内容。', duration_sec: 6, refs: [],
        }],
      }],
    };
    list.push(seg); save(); return seg;
  }
  function deleteSegment(pid, eid, sid) {
    const ep = getEpisode(pid, eid); if (!ep || !ep.storyboard) return;
    ep.storyboard.segments = ep.storyboard.segments.filter((s) => s.id !== sid);
    ep.storyboard.segments.forEach((s, i) => { s.order = i + 1; });
    save();
  }
  function updateShot(pid, eid, sid, sceneId, shotId, patch) {
    const seg = getSegment(pid, eid, sid); if (!seg) return;
    const sc = seg.scenes.find((c) => c.id === sceneId); if (!sc) return;
    const sh = sc.shots.find((x) => x.id === shotId); if (!sh) return;
    Object.assign(sh, patch);
    if (patch.plain != null) sh.rich = D.parseRich(patch.plain);
    save();
  }

  /* ---------- 剧本解析（对应线上 [剧本解析] → AI 回填概要 + 拆资产） ---------- */
  /** 从剧本正文提取场景头（如「1.内景 便利店 - 夜」） */
  function parseScenes(text) {
    const out = []; const re = /^\s*\d+\.\s*(内景|外景)\s*([^\-—]+?)\s*[-—]\s*(日|夜|清晨|黄昏|傍晚)\s*$/gm;
    let m; const seen = {};
    while ((m = re.exec(text || '')) !== null) {
      const name = m[2].trim();
      if (seen[name]) { seen[name].times.push(m[3]); continue; }
      seen[name] = { name: name, inter: m[1], times: [m[3]] };
      out.push(seen[name]);
    }
    return out;
  }
  /** 从剧本正文猜主要角色名（确定性规则：动作动词前 2-3 字 + 词频排序） */
  function detectCharacters(text) {
    const stop = { '我': 1, '他': 1, '她': 1, '它': 1, '他们': 1, '她们': 1, '镜头': 1, '画面': 1,
      '这时': 1, '突然': 1, '然后': 1, '最后': 1, '自动门': 1, '收银台': 1, '冰柜前': 1, '便利店里': 1 };
    const freq = {};
    const verbs = '把|说|看|走|站|坐|拿|转|发现|追|放|递|靠|起身|出门|问|找|贴|睡|进|停|掏|笑|盯|回头|低头|抬头|醒来';
    const re = new RegExp('([\\u4e00-\\u9fa5]{2,3})(?:' + verbs + ')', 'g');
    let m; while ((m = re.exec(text || '')) !== null) { const w = m[1]; if (!stop[w]) freq[w] = (freq[w] || 0) + 1; }
    // 过滤掉介词短语 / 方位词（如「从怀里」「货架间」）——它们不是人名
    const bad = /^(从|在|向|到|被|和|与|往|朝|沿|经|把|将|用)/;
    const badEnd = /(间|里|上|中|前|后|旁|边|处|口|内|外|时|中)$/;
    const cands = Object.keys(freq).filter(function (w) { return !bad.test(w) && !badEnd.test(w); })
      .sort(function (a, b) { return freq[b] - freq[a]; });
    // 归并：短名是长名前缀时视为同一角色（如「小周」/「小周追」）
    const out = [];
    cands.forEach(function (w) {
      if (!out.some(function (x) { return x.indexOf(w) === 0 || w.indexOf(x) === 0; })) out.push(w);
    });
    return out.slice(0, 3);
  }

  /** 分析剧本：回填 outline + 拆出角色/场景/道具资产（不含图片，图片由资产生成入口负责） */
  function analyzeScript(pid, styleCode, styleName, withAssets) {
    const p = getProject(pid); if (!p) return null;
    const ep = p.episodes[0];
    const text = ep.script || '';
    const scenes = parseScenes(text);
    // 角色名：按「动作动词前 2-3 字 + 词频」确定性提取
    const chars = detectCharacters(text);
    if (!chars.length) chars.push('主角');
    const firstLine = (text.split('\n').find(l => l.trim() && !/^\d+\./.test(l.trim())) || '').trim();
    // 回填概要
    p.outline = Object.assign({}, p.outline, {
      story_type: p.outline.story_type && p.outline.story_type !== '未设定' ? p.outline.story_type : '生活流 / 温情',
      target_audience: '成人',
      one_line_story: firstLine.slice(0, 60) || (ep.title + '的故事'),
      story_summary: text.replace(/^正文拍摄剧本\s*/, '').split('\n').filter(l => l.trim() && !/^\d+\./.test(l.trim())).join('').slice(0, 300),
      world_setting: scenes.length
        ? '现代' + scenes.map(s => s.inter + '·' + s.name).join('、') + '。时间跨度：' + [...new Set(scenes.flatMap(s => s.times))].join('→') + '。'
        : '单一内景，现实时空。',
      character_biographies: chars.map((c, i) => ({
        name: c, character_role: i === 0 ? 'protagonist' : 'support',
        bio: '剧中承担' + (i === 0 ? '主线行动与情感落点' : '关键冲突与对照') + '的角色（由剧本解析自动提取）。',
      })),
      outline_status: 'confirmed', editable: false,
    });
    if (styleCode) p.style = { style_id: D.nextId(), code: styleCode, name: styleName || '', cover_url: '' };
    if (withAssets) extractAssets(pid, chars, scenes, text);
    p.status = 'outline_confirmed';
    save();
    return { characters: withAssets ? p.assets.characters.length : 0, scenes: withAssets ? p.assets.scenes.length : 0, props: withAssets ? p.assets.props.length : 0 };
  }

  /** 从剧本拆出角色/场景/道具资产（线上第1步 [生成] →「正在分析资产/识别场景元素」） */
  function extractAssets(pid, chars, scenes, text) {
    const p = getProject(pid); if (!p) return null;
    const ep = p.episodes[0];
    if (!text) text = ep ? ep.script : '';
    if (!scenes || !scenes.length) scenes = parseScenes(text);
    if (!chars || !chars.length) chars = detectCharacters(text);
    if (!chars.length) chars = ['主角'];
    p.assets.characters = chars.map((c, i) => ({ id: D.nextId(), name: c, sort_order: i + 1, states: [] }));
    p.assets.scenes = scenes.slice(0, 4).map((sc, i) => ({
      id: D.nextId(), name: sc.name + (sc.times[0] ? '·' + sc.times[0] : ''), sort_order: i + 1, states: [],
    }));
    p.assets.props = [];
    const reProp = /([\u4e00-\u9fa5]{0,6}(?:照片|硬币|汽水|瓶子|标签|雨衣|U盘|戒指|信|钥匙))/g;
    const props = {}; let pm;
    while ((pm = reProp.exec(text)) !== null) props[pm[1]] = 1;
    Object.keys(props).slice(0, 3).forEach((n, i) => p.assets.props.push({ id: D.nextId(), name: n, sort_order: i + 1, states: [] }));
    p.status = 'assets_detected';
    save();
    return { characters: p.assets.characters.length, scenes: p.assets.scenes.length, props: p.assets.props.length };
  }

  /** 生成资产图片（对应线上 [生成全部角色图片] → 勾选 → 选择模型 → 生成） */
  function generateAssetImages(pid, kindList) {
    const p = getProject(pid); if (!p) return 0;
    let n = 0;
    (kindList || ['character', 'scene', 'prop']).forEach(function (kind) {
      const bucket = p.assets[kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props'] || [];
      bucket.forEach(function (a) {
        if (!a.states.length) {
          const img = Gen.keyframe({ seedKey: p.id + '|' + kind + '|' + a.name + '|基础形象', title: a.name, ratio: '1:1' });
          a.states.push({
            ref_id: D.nextId(), state_name: '基础形象', display_name: a.name + ' - 基础形象',
            token: '@[' + a.name + ' - 基础形象](sd-asset://' + kind + '/' + D.nextId() + ')',
            image: img, is_default: true, asset_type: kind,
          });
          n++;
        }
      });
    });
    if (n) save();
    return n;
  }

  /** 生成分镜脚本（对应线上分集卡 [生成分镜脚本]）：按剧本场景头拆镜组 */
  function generateStoryboard(pid, eid) {
    const p = getProject(pid); const ep = getEpisode(pid, eid);
    if (!p || !ep) return null;
    if (ep.storyboard && ep.storyboard.segments && ep.storyboard.segments.length) return ep.storyboard;
    const text = ep.script || '';
    const blocks = [];
    const re = /^\s*(\d+)\.\s*(内景|外景)\s*([^\-—]+?)\s*[-—]\s*(日|夜|清晨|黄昏|傍晚)\s*$/gm;
    let m, last = null;
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
      re.lastIndex = 0;
      const hm = re.exec(lines[i]);
      if (hm) { last = { no: hm[1], inter: hm[2], scene: hm[3].trim(), time: hm[4], body: [] }; blocks.push(last); continue; }
      if (last && lines[i].trim()) last.body.push(lines[i].trim());
    }
    const sceneAsset = {};
    (p.assets.scenes || []).forEach(function (s) { sceneAsset[s.name.split('·')[0]] = s; });
    const charAsset = {};
    (p.assets.characters || []).forEach(function (c) { charAsset[c.name] = c; });
    const segs = blocks.map(function (b, i) {
      const sc = sceneAsset[b.scene];
      const body = b.body.join(' ');
      const shots = body.split(/(?<=[。！？])/).filter(function (x) { return x.trim().length > 6; }).slice(0, 3)
        .map(function (x, j) {
          const refs = [];
          Object.keys(charAsset).forEach(function (cn) { if (x.indexOf(cn) >= 0) refs.push({ kind: 'character', name: cn + ' - 基础形象', id: charAsset[cn].id, image: (charAsset[cn].states[0] || {}).image || '' }); });
          if (sc) refs.push({ kind: 'scene', name: sc.name + ' - 基础形象', id: sc.id, image: (sc.states[0] || {}).image || '' });
          const rich = D.parseRich(x.trim()).concat([]);
          // 把角色/场景名替换为引用 token
          const richOut = [];
          let rest = x.trim();
          refs.forEach(function (r) {
            const bare = r.name.replace(/ - 基础形象$/, '');
            const idx = rest.indexOf(bare);
            if (idx >= 0) {
              if (idx > 0) richOut.push({ type: 'text', value: rest.slice(0, idx) });
              richOut.push({ type: 'ref', name: r.name, kind: r.kind, id: r.id });
              rest = rest.slice(idx + bare.length);
            }
          });
          if (rest) richOut.push({ type: 'text', value: rest });
          return {
            id: D.nextId(), order: j + 1, title: (x.trim().slice(0, 8)) || '镜次' + (j + 1),
            rich: richOut, plain: x.trim(), duration_sec: 5, refs: refs.map(function (r) { return { kind: r.kind, name: r.name, image: r.image, sort_order: 0 }; }),
          };
        });
      if (!shots.length) shots.push({ id: D.nextId(), order: 1, title: b.scene, rich: D.parseRich(body || '（空镜）'), plain: body || '（空镜）', duration_sec: 5, refs: [] });
      const secs = shots.reduce(function (a, s) { return a + s.duration_sec; }, 0);
      return {
        id: D.nextId(), order: i + 1, title: b.scene + '·' + b.time, summary: shots.map(function (s) { return s.plain; }).join(''),
        video_prompt: '参考图1为[' + (p.assets.characters[0] ? p.assets.characters[0].name + ' - 基础形象' : '角色') + ']的角色四视图。\n【剧情】' +
          shots.map(function (s, j) { return '【' + j * 4 + ' s - ' + ((j + 1) * 4) + ' s】' + s.plain; }).join('\n') +
          '\n\n视频全程不要字幕、不要屏幕文字；必须保留协调统一的全局BGM和必要环境音，禁止静音段。',
        duration_ms: secs * 1000, keyframe: '', video: '', status: 'script_ready',
        scenes: [{ id: D.nextId(), order: 1, title: b.scene + '（' + b.inter + '·' + b.time + '）', linked_asset_id: sc ? sc.id : '',
          rich: D.parseRich(sc ? '@[' + sc.name + ' - 基础形象](sd-asset://scene/' + sc.id + ')' : ''),
          plain: sc ? sc.name : '', refs: sc ? [{ kind: 'scene', name: sc.name + ' - 基础形象', image: (sc.states[0] || {}).image || '', sort_order: 1 }] : [],
          shots: shots }],
      };
    });
    ep.storyboard = { episode_id: eid, episode_no: ep.no, title: ep.title, phase: 'storyboard_script_ready', ratio: p.ratio, segments: segs };
    ep.script_status = 'completed';
    save();
    return ep.storyboard;
  }

  /* ---------- 导入 / 导出 / 重置 ---------- */
  function exportProject(pid) {
    const p = getProject(pid); if (!p) return;
    const blob = new Blob([JSON.stringify(p, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (p.name || 'project') + '.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function exportAll() {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'pavo-workbench-backup.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function importJSON(text) {
    const data = JSON.parse(text);
    if (data && data.projects) { state = data; save(); return { ok: true, scope: 'all' }; }
    if (data && data.id && data.episodes) {
      data.id = D.nextId();
      state.projects.unshift(data); save(); return { ok: true, scope: 'project' };
    }
    throw new Error('无法识别的文件结构');
  }
  function reset() {
    localStorage.removeItem(KEY);
    state = seed(); save();
  }

  /* ---------- 提示 ---------- */
  /* ---- 风格目录（取自线上「风格库」实测，分类：全部/2D/3D/真人/自定义风格） ---- */
  const STYLE_CATALOG = {
    '2D': ['儿童蜡笔手绘插画风格', '皮影戏插画风格', '黑白水墨风格', '赛博朋克数字插画风格',
           '90年代日式动画风格', '黑白二维漫画动画风格', '中国神话风格', '大友克洋风格',
           '手冢治虫时代卡通插画风格', '二次元动漫', '国漫二次元常用风格', '美国漫画动画插画风格'],
    '3D': ['美国3A游戏概念艺术风格', '3D渲染卡通风格', '黏土定格动画风格'],
    '真人': ['古偶唯美柔光风格', '美式复古影视风格', '都市言情风格', '黑白胶片摄影风格',
             '90年代写实电影风格', '蓝橙色调影视风格', '悬疑电影风格', '韩国冷淡风电影风格',
             '恐怖电影风格', '90年代港片风格', '宫斗权谋冷峻风格', '韩剧都市柔光风格',
             '80年代中国农村电影风格', '荒野电影风格', '真人古风写实风格', '美式经济上行风格'],
    '自定义风格': [],
  };

  /* ---------- 远端水合（仅 `http` 驱动调用）---------------------------------
   * 为什么是「水合」而不是改视图：
   *   各视图有 30+ 处**直接读** `S.getProject()` / `S.state` / `S.STYLE_CATALOG`。
   *   与其逐处改成 `Api.getProgress()`（改动面大、容易漏、离线时还得逐处写回落），
   *   不如在**唯一的渲染入口**（`app.js` 的 render）先把远端数据灌进 Store，
   *   于是**视图一行都不用动**，`local` 驱动行为也完全不变。
   * ---------------------------------------------------------------------- */

  /** Store 的项目/集**不变量**：视图会直接取这些键，缺了就是整页崩（实测两次）。
   *  前端**不依赖服务端给全** —— 部分接口天然更轻（如列表不返回 outline）。 */
  function _defaultOutline() {
    return {
      story_type: '', target_audience: '', one_line_story: '', story_summary: '',
      world_setting: '', character_biographies: [],
      outline_status: 'draft', editable: true,
    };
  }
  function _defaultFlow() {
    return {
      current_step: 'outline',
      allowed_actions: [], blocked_actions: [], recommended_actions: [],
    };
  }
  function _backfillProject(p) {
    if (!p.outline || typeof p.outline !== 'object') p.outline = _defaultOutline();
    if (!p.flow || typeof p.flow !== 'object') p.flow = _defaultFlow();
    if (!p.assets || typeof p.assets !== 'object') p.assets = { characters: [], scenes: [], props: [] };
    if (!p.style || typeof p.style !== 'object') {
      p.style = { style_id: '', code: '', name: '', cover_url: '' };
    }
    if (!Array.isArray(p.episodes)) p.episodes = [];
    return p;
  }

  /** 合并单个项目（不落盘）。空壳**不会**冲掉本地已有的分镜。 */
  function _mergeProject(p) {
    if (!p || !p.id) return null;
    const cur = state.projects.find(function (x) { return x.id === p.id; }) || {};
    const next = Object.assign({}, cur, p);
    if (Array.isArray(p.episodes)) {
      next.episodes = p.episodes.map(function (ep, i) {
        const old = (cur.episodes || []).find(function (o) { return o.id === ep.id; })
          || (cur.episodes || [])[i] || {};
        const merged = Object.assign({}, old, ep);
        // 列表/概览接口只给 storyboard 的**轻量壳**（segments 为空）——
        // 别把本地已经取到的完整分镜冲掉（那会让分镜页闪空）
        const hadSegs = old.storyboard && (old.storyboard.segments || []).length;
        const newSegs = ep.storyboard && (ep.storyboard.segments || []).length;
        if (hadSegs && !newSegs) merged.storyboard = old.storyboard;
        // ★ 兜底不变量：Store 约定「每集必有 storyboard」
        //   （各视图直接取 `ep.storyboard.segments`，见 `playlet-list.js:140`）。
        //   不依赖服务端一定给这个键 —— 新项目在 Store 里没有旧值可回落，
        //   缺键会让视图在 `.segments` 上抛 TypeError（实测：整页崩）。
        if (!merged.storyboard || typeof merged.storyboard !== 'object') {
          merged.storyboard = {
            episode_id: merged.id, episode_no: merged.no,
            title: merged.title || '', phase: 'draft', ratio: '9:16', segments: [],
          };
        } else if (!Array.isArray(merged.storyboard.segments)) {
          merged.storyboard = Object.assign({}, merged.storyboard, { segments: [] });
        }
        return merged;
      });
    }
    _backfillProject(next);
    const idx = state.projects.findIndex(function (x) { return x.id === p.id; });
    if (idx >= 0) state.projects[idx] = next; else state.projects.push(next);
    return next;
  }

  /** 水合单个项目（会落盘）。 */
  function upsertProject(p) {
    if (!state) load();
    const r = _mergeProject(p);
    if (r) save();
    return r;
  }

  /** 水合一批项目。
   *
   * ★ `replace`（列表页用）：**以服务端为全集** —— 不在返回里的项目会被移除。
   *   为什么必须这样（2026-09-15 真实浏览器实测）：默认的「只增不减」合并会把
   *   **出厂种子项目（借脸）留在列表里**，与 19 个真项目混在一起 ——
   *   界面上分不出哪个是真、哪个是 mock，而且种子的图片路径（`_assets/…`）
   *   在真实项目里根本不存在 → 一堆 404 + 坏图。
   *   「有哪些项目」这件事，后端是唯一真相源。
   *
   *   注意：**只在列表接口这么做**。`/progress` 单项目水合仍是合并（要保住已取到的分镜）。
   */
  function upsertProjects(list, replace) {
    if (!state) load();
    if (!Array.isArray(list)) return 0;
    if (replace) {
      const keep = new Set(list.map(function (p) { return p && p.id; }));
      state.projects = state.projects.filter(function (p) { return keep.has(p.id); });
    }
    list.forEach(_mergeProject);
    save();                       // 整批只落盘一次
    return list.length;
  }

  /** 水合某一集的完整分镜（`detail` 端点）。 */
  function upsertStoryboard(pid, eid, sb) {
    if (!state) load();
    if (!sb) return null;
    const p = getProject(pid); if (!p) return null;
    const ep = (p.episodes || []).find(function (e) { return e.id === eid; });
    if (!ep) return null;
    ep.storyboard = Object.assign({}, ep.storyboard || {}, sb);
    save();
    return ep.storyboard;
  }

  /** 水合风格库（http 驱动下用**后端的真实类型包**替换出厂目录）。
   *
   * 为什么要替换而不是并列：v5 **只有 3 个类型包**，而前端出厂目录是 Pavo 线上的
   * 20+ 个中文风格名。两套混着用 = **选了 A 实际跑 B**（静默偏差，本项目最忌）。
   * 所以 http 驱动下以 `/styles` 为准；local 驱动保留出厂目录（离线可用）。
   */
  function setStyles(list) {
    if (!state) load();
    if (!Array.isArray(list) || !list.length) return 0;
    state.styles = list;
    save();
    return list.length;
  }

  /** 厂商档：**派生数据**，与风格库同策（每次 load() 清空，等水合重灌）。
   *
   * 为什么不能靠缓存：厂商列表由**后端**决定（`GET /vendors` ←
   * `v5.media.vendors.register()`）。公司机注册了 `comfy` 之后，
   * 前端若还在读 localStorage 里的旧列表，就**看不到新厂商**（静默偏差）。
   *
   * local 驱动下**没有厂商列表**（生成是 Canvas 占位、根本不调模型）
   * ⇒ 视图据此把选择器**禁用并说明**，而不是假装它能用。
   */
  function setVendors(v) {
    if (!state) load();
    if (!v || typeof v !== 'object' || !Array.isArray(v.items)) return 0;
    state.vendors = v;
    save();
    return v.items.length;
  }

  function toast(msg, kind) {
    let wrap = document.querySelector('.toast-wrap');
    if (!wrap) { wrap = D.el('div', { class: 'toast-wrap' }); document.body.appendChild(wrap); }
    const node = D.el('div', { class: 'toast', html: (kind === 'ok' ? '<span class="ok">✓</span>' : '') + D.esc(msg) });
    wrap.appendChild(node);
    setTimeout(function () {
      node.style.transition = 'opacity .3s'; node.style.opacity = '0';
      setTimeout(() => node.remove(), 320);
    }, 2200);
  }

  global.Store = {
    STYLE_CATALOG: STYLE_CATALOG,
    analyzeScript: analyzeScript,
    generateAssetImages: generateAssetImages,
    generateStoryboard: generateStoryboard,
    extractAssets: extractAssets,
    load, save, subscribe, toast,
    get state() { return state; },
    allProjects, getProject, getEpisode, getSegment, episodeStats,
    createProject, updateProject, deleteProject, duplicateProject,
    updateScript, renameEpisode, addEpisode, deleteEpisode,
    addAsset, removeAsset, renameAsset, addAssetState, removeAssetState, addAssetRef,
    updateSegment, setSegmentMedia, addSegment, deleteSegment, updateShot,
    exportProject, exportAll, importJSON, reset,
    upsertProject, upsertProjects, upsertStoryboard, setStyles, setVendors,
  };
})(window);
