/* ==========================================================================
   frontend/mock/mock-api.mjs —— 假后端（纯函数，可单测）
   --------------------------------------------------------------------------
   为什么不直接在浏览器里 patch `window.fetch`：
     `<img src="/media/...">` 的加载**不走 fetch**，走的是浏览器资源加载管线。
     patch fetch 只能桩住 XHR/fetch 那一半，封面永远是破图
     —— 而"封面显示不出来"恰好是最想在这轮里验的东西之一。
   ⇒ 桩打在 **CDP 的 `Fetch` 域**（网络层），`/v1` 与 `/media` 一起桩。
   ========================================================================== */

/**
 * 1×1 **全透明** PNG —— 让封面"能加载"，从而把逻辑与素材分开看。
 *
 * ⚠️ 第一版用的是网上抄来的 `iVBORw0KGgo...X8jx0gAAAABJRU5ErkJggg==`，
 * 那其实是个**红点** —— 被 `object-fit: cover` 拉满整张卡片后，截图里一片刺红，
 * 看截图的人（包括我自己）第一反应是"这里坏了"。占位素材要选"看起来就是占位"的。
 * 这一段是现算的（IHDR 1×1 / 8bit / RGBA + IDAT(filter0 + 全透明) + IEND，70 字节）。
 */
export const PIXEL_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg==';

const SCRIPT = [
  '# 第1集：一个音的钱',
  '> 系列：测试',
  '> 类型包：niulai-movie-style',
  '',
  '## 场次列表',
  '',
  '### 第1场 教堂 - 日',
  '**场景：** 圣托马斯教堂 · 唱诗班席 日',
  '画面：石砌拱顶压得很低。',
  '<画面>',
  '一束光落在管风琴上。',
  '巴赫（严肃、不紧不慢）',
  '把谱子放下。',
  '',
].join('\n');

function mkSegments(id) {
  return [1, 2, 3].map((n) => ({
    id: `${id}-LN0${n}`,
    order: n,
    title: n === 1 ? '全景·唱诗班席' : '中景·管风琴前',
    duration_ms: 4000 + n * 1000,
    keyframe: n === 1 ? '/media/mock/kf1.jpg' : '',
    video: '',
    scenes: [{
      id: `${id}-LN0${n}-S1`,
      title: '唱诗班席与管风琴前',
      shots: [{ title: `镜头 ${n}`, duration_sec: 8, rich: [{ type: 'text', value: '他推开门。\n' }] }],
    }],
  }));
}

const project = (id, name, eps, cover) => ({
  id,
  name,
  ratio: '9:16',
  style: { code: 'niulai-movie-style', name: '牛来电影风格', cover_url: '' },
  status: 'completed',
  cover,
  created_at: '2026-09-19 12:00',
  auto_sync_assets: false,
  outline: {
    story_type: '温情喜剧',
    story_summary: '一段测试概要。',
    world_setting: '十八世纪莱比锡。',
    character_biographies: [{ name: '巴赫', bio: '乐长，务实。' }],
  },
  episodes: Array.from({ length: eps }, (_, i) => ({
    id: `${id}-ep${i + 1}`,
    no: i + 1,
    title: `第${i + 1}集`,
    script: SCRIPT,
    script_status: 'completed',
    storyboard: { ratio: '9:16', segments: mkSegments(`${id}-ep${i + 1}`) },
  })),
  assets: {
    characters: [{
      id: `${id}-c1`, name: '巴赫', identity: '宽额、粗大手、深褐长袍',
      states: [{ ref_id: `${id}-c1-r1`, is_default: true, state_name: '基础形象', image: '/media/mock/c1.jpg' }],
    }],
    scenes: [{ id: `${id}-s1`, name: '圣托马斯教堂', states: [{ ref_id: `${id}-s1-r1`, is_default: true, state_name: '基础形象' }] }],
    props: [{ id: `${id}-p1`, name: '铜烛台', states: [] }],
  },
});

export const MOCK_STYLES = [
  { code: 'niulai-movie-style', name: '牛来电影风格', group: '写实',
    visual_style: '电影感打光，浅景深，35mm 颗粒',
    cover_url: '/media/p-mock-1/media/ep1/stills/LN01.jpg',
    sample_topic: '羊毛毡测试集', sample_shot: '（LN01）', sample_stills: 12 },
  { code: 'paper-crane-style', name: '纸艺定格', group: '动画', visual_style: '手工纸质感，暖色低饱和' },
  { code: 'felt-style', name: '羊毛毡故事', group: '动画', visual_style: '羊毛毡质感，柔光' },
];

export const MOCK_VENDORS = {
  items: [
    { code: 'agnes', name: 'Agnes（默认）', short: 'Agnes', builtin: true },
    { code: 'comfy', name: '公司内网 ComfyUI', short: '公司内网 ComfyUI', builtin: false },
  ],
  current: { image: 'agnes', video: 'agnes' },
  current_ok: { image: true, video: true },
};

/** 每个假后端实例自己的可变状态（测试之间互不污染）。 */
export function createMockState() {
  return {
    projects: [
      project('p-mock-1', '羊毛毡测试集', 3, '/media/p-mock-1/media/ep1/stills/LN01.jpg'),
      project('p-mock-2', '纸鹤', 1, '/media/p-mock-2/media/ep1/stills/LN01.jpg'),
      project('p-mock-3', '无封面项目', 0, ''),
    ],
    /** `true` ⇒ 「精选项目」那条返回 500（用来验"拉取失败"那一档文案）。 */
    failFeatured: false,
    /** `true` ⇒ 链路"停在某一步等人"（用来验底部那条人机确认条）。 */
    hitlPending: false,
    /** 记录 postHitl 收到的决定 */
    decisions: [],
    /** 记录收到的每个请求：用来断言"没有意外请求 / 没有 404 循环"。 */
    log: [],
    aiCreated: 0,
    pasted: 0,
    renamed: [],
    deleted: [],
  };
}

const ok = (data) => ({ status: 200, json: { code: '000000', message: 'success', data } });

/**
 * 路由一个请求。**纯函数**：`(url, method, body, state) → 响应描述`。
 * 返回 `{status, json}` 或 `{status, image:true}`。
 */
export function route(rawUrl, method, body, st) {
  const u = new URL(rawUrl, 'http://mock.local');
  const p = u.pathname;
  st.log.push({ method, path: p + (u.search || ''), at: Date.now() });

  // ---- 静态素材 ----
  if (p.startsWith('/media/')) return { status: 200, image: true };

  // ---- 探活 ----
  if (p === '/health') return ok({ ok: true, version: 'mock' });

  // ---- 预设 ----
  if (p === '/v1/pixa/short-drama/styles') return ok(MOCK_STYLES);
  if (p === '/v1/pixa/short-drama/vendors') return ok(MOCK_VENDORS);

  // ---- 项目列表 ----
  if (p === '/v1/pixa/short-drama/projects') {
    const isDemo = u.searchParams.get('is_demo') === 'true';
    if (isDemo) {
      if (st.failFeatured) {
        return { status: 500, json: { code: '500', message: '假后端：精选接口故障（故意）' } };
      }
      return ok({ list: [], total: 0 });       // 后端确实没有精选 ⇒ 走"暂无"那一档
    }
    return ok({ list: st.projects, total: st.projects.length });
  }

  // ---- 项目详情 ----
  let m = /^\/v1\/pixa\/short-drama\/projects\/([^/]+)\/progress$/.exec(p);
  if (m) {
    const hit = st.projects.find((x) => x.id === decodeURIComponent(m[1]));
    if (!hit) return { status: 404, json: { code: '404', message: '项目不存在：' + m[1] } };
    return ok(hit);
  }

  m = /^\/v1\/pixa\/short-drama\/projects\/([^/]+)\/hitl$/.exec(p);
  if (m) {
    if (method === 'POST') {
      st.decisions.push(body || {});
      st.hitlPending = false;              // 提交决定后挂起就解除
      return ok({ pending: false, manual_steps: true, redo_targets: [] });
    }
    if (!st.hitlPending) {
      return ok({ pending: false, manual_steps: true, redo_targets: [], prev_role: '', next_role: '' });
    }
    return ok({
      pending: true, stamp: 'stamp-1', manual_steps: true,
      prev_role: 'scriptwriter', next_role: 'dialogue',
      redo_targets: ['director', 'worldbuilder', 'scriptwriter'],
    });
  }

  m = /^\/v1\/pixa\/short-drama\/projects\/([^/]+)\/rename$/.exec(p);
  if (m && method === 'POST') {
    const id = decodeURIComponent(m[1]);
    const hit = st.projects.find((x) => x.id === id);
    if (!hit) return { status: 404, json: { code: '404', message: '项目不存在：' + id } };
    if (body && typeof body.name === 'string') hit.name = body.name;
    st.renamed.push({ id, name: body && body.name });
    return ok(true);
  }

  if (p === '/v1/pixa/short-drama/projects/batch-delete' && method === 'POST') {
    const ids = (body && body.project_ids) || [];
    st.projects = st.projects.filter((x) => !ids.includes(x.id));
    st.deleted.push(...ids);
    return ok(true);
  }

  if (p === '/v1/pixa/short-drama/projects/ai-generate' && method === 'POST') {
    st.aiCreated++;
    const id = 'p-mock-ai';
    if (!st.projects.some((x) => x.id === id)) st.projects.push(project(id, 'AI创作结果', 1, ''));
    return ok({ id, topic: 'AI创作结果' });
  }

  if (p === '/v1/pixa/short-drama/projects/paste' && method === 'POST') {
    st.pasted++;
    const id = 'p-mock-paste';
    if (!st.projects.some((x) => x.id === id)) st.projects.push(project(id, '粘贴剧本结果', 1, ''));
    return ok({ id, analyze: { characters: 3, scenes: 2, props: 1 } });
  }

  // ---- 分集 / 分镜 ----
  m = /^\/v1\/pixa\/short-drama\/episodes\/([^/]+)\/storyboard\/detail$/.exec(p);
  if (m) {
    const eid = decodeURIComponent(m[1]);
    const pid = eid.replace(/-ep\d+$/, '');
    const proj = st.projects.find((x) => x.id === pid);
    const ep = proj && (proj.episodes || []).find((e) => e.id === eid);
    if (!ep) return { status: 404, json: { code: '404', message: '分集不存在：' + eid } };
    return ok({ episode_id: eid, episode_no: ep.no, title: ep.title, ratio: '9:16',
                segments: (ep.storyboard && ep.storyboard.segments) || [] });
  }

  // 段级写操作（编辑镜头 / 新增 / 删除）
  m = /^\/v1\/pixa\/short-drama\/segments$/.exec(p);
  if (m && method === 'POST') return ok(true);
  m = /^\/v1\/pixa\/short-drama\/segments\/([^/]+)$/.exec(p);
  if (m) return ok(true);
  m = /^\/v1\/pixa\/short-drama\/episodes\/([^/]+)\/segments$/.exec(p);
  if (m && method === 'POST') return ok({ id: 'new-seg' });

  // 取消 run / 查 run（过场上的「停止」与轮询用）
  m = /^\/v1\/pixa\/short-drama\/runs\/([^/]+)$/.exec(p);
  if (m) return ok({ run_id: m[1], status: method === 'DELETE' ? 'cancelled' : 'running' });

  // 资产相关写操作
  if (/^\/v1\/pixa\/short-drama\/projects\/[^/]+\/assets($|\/)/.exec(p)) return ok({ applied: {}, ignored: [] });
  if (/^\/v1\/pixa\/short-drama\/projects\/[^/]+\/outline$/.exec(p)) return ok(true);

  m = /^\/v1\/pixa\/short-drama\/runs/.exec(p);
  if (m) return ok({ list: [] });

  // 其余一律 404 —— 但**会被断言抓出来**（"意外的请求"本身就是判据）
  return { status: 404, json: { code: '404', message: '假后端未实现：' + method + ' ' + p } };
}
