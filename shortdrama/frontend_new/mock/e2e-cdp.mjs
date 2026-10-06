/* ==========================================================================
   frontend/mock/e2e-cdp.mjs —— 用 CDP 跑一轮「假后端 + 真人点击」的端到端
   --------------------------------------------------------------------------
   为什么要这样测（而不是继续用 jsdom 单测）：
     jsdom 测的是"组件被调用后 DOM 对不对"，它**测不出**：
       · 元素在不在屏幕上、会不会被固定底栏压住（点击坐标打得到吗）
       · 事件委托/命中测试/焦点在真实浏览器里的行为
       · hash 路由切换、Portal 弹层、边缘热区这类"跟浏览器绑定"的东西
     ⇒ 这一层用**真实鼠标事件**（`Input.dispatchMouseEvent`，走浏览器输入管线，
       而不是 `element.click()`）来跑，补上 jsdom 与人工目视之间的那道缝。

   它补的是旧版 skill 里那三层验证的**第三层**（真浏览器量测），
   只是把"后端"换成 `mock-api.mjs` —— 于是不依赖任何真实服务、零配额、可重复。

   用法：
     node frontend/mock/e2e-cdp.mjs                 # 需要先起 vite dev (5173)
     CDP_PORT=9333 node frontend/mock/e2e-cdp.mjs   # Chrome 调试端口
   ========================================================================== */

import { createMockState, route, PIXEL_PNG_B64 } from './mock-api.mjs';

const PORT = Number(process.env.CDP_PORT || 9333);
const APP = process.env.APP_URL || 'http://127.0.0.1:5173';

/* ------------------------------------------------------------------ CDP 底座 */

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find((t) => t.type === 'page' && !t.url.startsWith('devtools://'));
if (!page) { console.error('ERROR: 找不到可用的 page target（Chrome 没起？）'); process.exit(1); }

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.addEventListener('open', res);
  ws.addEventListener('error', () => rej(new Error('ws connect failed')));
});

let seq = 0;
const pending = new Map();
const eventHandlers = new Map();
/** 控制台/异常收集 —— 「零报错」必须是真的零，而不是没接上监听。 */
const pageErrors = [];

ws.addEventListener('message', (ev) => {
  let m; try { m = JSON.parse(ev.data); } catch { return; }
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id);
    pending.delete(m.id);
    m.error ? reject(new Error(m.method + ' ' + JSON.stringify(m.error))) : resolve(m.result);
    return;
  }
  if (m.method === 'Runtime.exceptionThrown') {
    pageErrors.push('exception: ' + (m.params.exceptionDetails?.exception?.description
      || m.params.exceptionDetails?.text || '?'));
  }
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
    pageErrors.push('console.error: ' + m.params.args.map((a) => a.value ?? a.description ?? '').join(' '));
  }
  const h = eventHandlers.get(m.method);
  if (h) h(m.params);
});

const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
const on = (method, fn) => eventHandlers.set(method, fn);

async function evaluate(expr, awaitPromise = true) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) {
    throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  }
  return r.result.value;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(expr, { timeout = 6000, label = expr } = {}) {
  const t0 = Date.now();
  let last;
  while (Date.now() - t0 < timeout) {
    try {
      last = await evaluate(expr);
      if (last) return last;
    } catch (e) { last = 'ERR ' + e.message; }
    await sleep(120);
  }
  throw new Error(`waitFor 超时（${timeout}ms）：${label}；最后一次 = ${JSON.stringify(last)}`);
}

/* ---------------------------------------------------- 真人操作（真实输入事件） */

/** 取元素中心的**视口坐标**（先把元素滚进视野）。 */
async function centerOf(sel) {
  const box = await evaluate(`(() => {
    const e = document.querySelector(${JSON.stringify(sel)});
    if (!e) return null;
    e.scrollIntoView({ block: 'center', inline: 'center' });
    const b = e.getBoundingClientRect();
    if (!b.width || !b.height) return null;
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, w: b.width, h: b.height };
  })()`);
  if (!box) throw new Error('找不到可点的元素：' + sel);
  return box;
}

async function moveMouse(x, y) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none' });
}

/**
 * ★★ 点击前先做**命中测试**。
 *
 * 为什么值得加这一步：真点击失败时的症状是"点了没反应"，信息量为零，
 * 会把人引向"前端点击坏了"（第一轮我就差点这么判）。而 `elementFromPoint`
 * 能一句话说清："你点的那一格，最上层其实是另一个元素" ——
 * 这与旧 skill 里「存在 ≠ 可用」「元素被固定底栏压住」是同一条纪律。
 */
async function assertHittable(sel, x, y) {
  const top = await evaluate(`(() => {
    const e = document.querySelector(${JSON.stringify(sel)});
    const t = document.elementFromPoint(${x}, ${y});
    if (!t) return { ok: false, what: 'null（坐标在视口外？）' };
    const ok = (t === e) || e.contains(t) || t.contains(e);
    return { ok, what: t.tagName + '.' + (t.className || '') + ' :: ' + (t.textContent || '').trim().slice(0, 24) };
  })()`);
  if (!top.ok) throw new Error(`坐标 (${Math.round(x)},${Math.round(y)}) 上最上层不是目标 ${sel} ⇒ ${top.what}`);
}

/**
 * ★ 真实点击：走 `Input.dispatchMouseEvent`，浏览器会做**命中测试**，
 *   所以"元素被别的层盖住"这种情况会真的点不到（`element.click()` 则照样"成功"，
 *   那正是旧 skill 里"断言全绿但按钮其实被压住"的成因）。
 */
async function clickReal(sel, { settle = 220, hitTest = true } = {}) {
  const c = await centerOf(sel);
  if (hitTest) await assertHittable(sel, c.x, c.y);
  await moveMouse(c.x, c.y);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', buttons: 1, clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x, y: c.y, button: 'left', buttons: 0, clickCount: 1 });
  await sleep(settle);
  return c;
}

/** 清掉上一次留下的标记（**必须**：标记不唯一 ⇒ querySelector 会点到别处，第二轮就栽在这）。 */
const clearTarget = () => evaluate(
  `(() => { document.querySelectorAll('[data-e2e-target]').forEach(e => e.removeAttribute('data-e2e-target')); return 1; })()`,
);

/** 收尾清理：元素可能**因为点击生效而被卸载**，所以不许无条件 removeAttribute。 */
const dropTarget = () => evaluate(
  `(() => { const e = document.querySelector('[data-e2e-target="1"]'); if (e) e.removeAttribute('data-e2e-target'); return e ? 1 : 0; })()`,
);

/** 按**文字**点（挑面积最小的那个匹配元素，避免点到外层容器）。 */
async function clickText(text, tag = 'button') {
  await clearTarget();
  const sel = await evaluate(`(() => {
    const t = ${JSON.stringify(text)};
    const list = [...document.querySelectorAll(${JSON.stringify(tag)})]
      .filter(e => (e.textContent || '').trim() === t);
    if (!list.length) return null;
    list.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return ra.width * ra.height - rb.width * rb.height;
    });
    list[0].setAttribute('data-e2e-target', '1');
    return '[data-e2e-target="1"]';
  })()`);
  if (!sel) throw new Error('按文字找不到元素：' + text);
  const c = await clickReal(sel);
  await dropTarget();
  return c;
}

/** 点「⋯」这类同名多个的元素，按序号挑。 */
async function clickNth(sel, n = 0) {
  await clearTarget();
  await evaluate(`(() => {
    const list = document.querySelectorAll(${JSON.stringify(sel)});
    if (!list[${n}]) throw new Error('没有第 ${n} 个 ' + ${JSON.stringify(sel)});
    list[${n}].setAttribute('data-e2e-target', '1');
  })()`);
  const c = await clickReal('[data-e2e-target="1"]');
  await dropTarget();
  return c;
}

async function pressKey(key, code, vk, modifiers = 0) {
  for (const type of ['keyDown', 'keyUp']) {
    await send('Input.dispatchKeyEvent', { type, key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers });
  }
  await sleep(80);
}

/** 打字：先真实点一下取得焦点，再用 Input.insertText（会真的产生 input 事件）。 */
async function typeInto(sel, text) {
  await clickReal(sel, { settle: 120 });
  await evaluate(`document.activeElement && document.activeElement.setAttribute('data-e2e-focus','1')`);
  const focused = await evaluate(`(() => {
    const e = document.querySelector('[data-e2e-focus="1"]');
    return e ? (e.tagName + '|' + (e.className || '')) : 'NONE';
  })()`);
  await evaluate(`document.querySelectorAll('[data-e2e-focus]').forEach(e=>e.removeAttribute('data-e2e-focus'))`);
  if (focused === 'NONE') throw new Error('点击后没有拿到焦点：' + sel);
  await send('Input.insertText', { text });
  await sleep(200);
}

/* ------------------------------------------------------- 假后端（网络层桩） */

const st = createMockState();
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');

on('Fetch.requestPaused', async (p) => {
  const { requestId, request } = p;
  let body = null;
  if (request.postData) { try { body = JSON.parse(request.postData); } catch { body = request.postData; } }
  const r = route(request.url, request.method, body, st);
  const last = st.log[st.log.length - 1];
  if (last) last.status = r.status;
  try {
    if (r.image) {
      await send('Fetch.fulfillRequest', {
        requestId, responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'image/png' }],
        body: PIXEL_PNG_B64,
      });
    } else {
      await send('Fetch.fulfillRequest', {
        requestId, responseCode: r.status,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: b64(JSON.stringify(r.json)),
      });
    }
  } catch (e) {
    console.error('[mock] fulfill 失败：', e.message);
  }
});

/* ------------------------------------------------------------------ 断言框架 */

const results = [];
function check(name, okFlag, detail = '') {
  results.push({ name, ok: !!okFlag, detail });
  console.log(`${okFlag ? '  ✓' : '  ✗'} ${name}${detail ? '  — ' + detail : ''}`);
  return !!okFlag;
}
async function step(title, fn) {
  console.log('\n▶ ' + title);
  try { await fn(); } catch (e) {
    check(title + '（步骤本身抛错）', false, e.message);
  }
}

const navLog = [];
const nav = async (hash = '#/playlet/list') => {
  navLog.push(Date.now());
  await send('Page.bringToFront');
  return evaluate(`(async () => { location.href = ${JSON.stringify(APP)} + '/?cb=' + Date.now() + ${JSON.stringify(hash)}; return 1; })()`);
};
// ⚠️ hash SPA 的 `location.href` 改动若只换 hash 不会重新加载文档 ⇒ 必须带 `?cb=`
//    （旧 skill 记过这条：`Page.navigate` 到另一个 hash 是 same-document 导航，跑的还是旧 JS）

/* ------------------------------------------------------------------ 开跑 */

await send('Page.enable');
await send('Runtime.enable');
await send('Fetch.enable', {
  patterns: [
    { urlPattern: '*/v1/*', requestStage: 'Request' },
    { urlPattern: '*/media/*', requestStage: 'Request' },
    { urlPattern: '*/health', requestStage: 'Request' },
  ],
});

/**
 * ★★ **这两句是必须的，而且第一轮就是把它们漏了**（2026-09-19 实测，白跑一轮）。
 *
 *   症状：`Input.dispatchMouseEvent` 的 press/release **全部石沉大海** ——
 *   页面完全没反应（tab 不切、弹窗不开），而同一坐标 `document.elementFromPoint`
 *   返回的正是那个按钮、改用 `element.click()` 又能生效。
 *   ⇒ 极容易被读成"React 的点击坏了"，其实是**页面不在前台 / 没聚焦时，
 *     浏览器不把合成输入路由给渲染进程**。
 *
 *   探针实测（四种写法各跑一次）：
 *     V1 直接派发                       → ✗
 *     V2 先 `Page.bringToFront`         → ✓
 *     V3 先 `Emulation.setFocusEmulationEnabled` → ✓
 *     V4 两者都做                       → ✓
 *   这里两个都做：`bringToFront` 解决"窗口不是前台"，
 *   `setFocusEmulationEnabled` 顺带让页面**自认为已聚焦** —— 后者还能缓解
 *   旧 skill 记过的"后台标签页定时器被节流"（轮询类 UI 看起来不更新）。
 */
await send('Page.bringToFront');
await send('Emulation.setFocusEmulationEnabled', { enabled: true });

console.log('=== 假后端 + 真人点击 E2E（后端 8787 未启动）===');
console.log('app =', APP, ' cdp =', PORT);
const t0 = Date.now();
// 清掉上一次运行留下的水合埋点（sessionStorage 跨导航存活，不清会串场）
try { await evaluate(`(() => { sessionStorage.removeItem('__hydrations'); return 1; })()`); } catch { /* 可能还在 about:blank */ }

await step('S1 冷启动：假后端喂 3 个项目', async () => {
  await nav();
  await sleep(1500);
  await waitFor(`document.querySelectorAll('.project-card').length === 3`, { label: '3 张项目卡' });
  check('渲染 3 张项目卡', true);
  const names = await evaluate(`[...document.querySelectorAll('.project-card h3')].map(e=>e.textContent)`);
  check('卡片名称来自假后端', JSON.stringify(names) === JSON.stringify(['羊毛毡测试集', '纸鹤', '无封面项目']), JSON.stringify(names));
  check('无封面项目显示「暂无封面」',
    await evaluate(`document.querySelectorAll('.project-cover .muted').length === 1`));
  const imgs = await waitFor(
    `(() => { const a = [...document.querySelectorAll('.project-cover img')].map(i => i.naturalWidth);
      return a.length === 2 && a.every(n => n > 0) ? a : 0; })()`,
    { timeout: 5000, label: '两张封面加载完成' },
  );
  // ★ 这里必须**等**，不能立刻读：`<img loading="lazy">` 的请求是渲染后异步发的，
  //   刚渲染完的那一帧 `naturalWidth` 一定是 0。第一轮我按"立刻读"写，
  //   拿到 [0,0] 就以为"桩没覆盖到 <img>"，跑去加了探针 —— 而探针里等一会儿读就是 1。
  check('两张封面真的加载成功（桩打在网络层，<img> 也被覆盖）',
    JSON.stringify(imgs) === '[1,1]', JSON.stringify(imgs));
  check('渲染期无异常（__APPERR 为空）', (await evaluate('(window.__APPERR||[]).length')) === 0);
  check('后端连通 ⇒ 不显示「后端未连通」横幅',
    (await evaluate(`document.body.innerText.includes('后端未连通，显示本地缓存')`)) === false);
});

await step('S2 真实鼠标：贴左边 → 侧栏弹出；移开 → 收起', async () => {
  await moveMouse(3, 450);
  const opened = await waitFor(`document.querySelector('.sider').classList.contains('is-open')`,
    { label: '侧栏 is-open' }).then(() => true).catch(() => false);
  check('鼠标贴左边缘 ⇒ .sider.is-open', opened,
    await evaluate(`document.querySelector('.sider').className`));
  check('把手在面板展开时隐藏',
    await evaluate(`document.querySelector('.shell-handle--left').classList.contains('is-hidden')`));
  await moveMouse(700, 450);
  const closed = await waitFor(`!document.querySelector('.sider').classList.contains('is-open')`,
    { label: '侧栏收起' }).then(() => true).catch(() => false);
  check('鼠标移开 ⇒ 侧栏收起', closed);
});

await step('S3 点「AI生成剧本」→ 面板与风格预览', async () => {
  await clickText('AI生成剧本');
  await waitFor(`!!document.querySelector('#ai-idea')`, { label: 'AI 输入框' });
  check('AI 面板出现（#ai-idea 存在）', true);
  check('风格库下拉有 3 个包',
    (await evaluate(`document.querySelectorAll('#ai-style option').length`)) === 3);
  check('「开始创作」在没输入时是禁用的',
    await evaluate(`document.querySelectorAll('button')[0] && [...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='开始创作'&&b.disabled)`));
  check('风格预览卡存在且带真实样张',
    await evaluate(`!!document.querySelector('.style-prev img')`));
  check('预览卡写明样张出处',
    (await evaluate(`(document.querySelector('.style-prev-meta')||{}).textContent || ''`)).includes('羊毛毡测试集'));
});

await step('S4 真敲字 → 字数计数与按钮可用态联动', async () => {
  const idea = '一个被裁掉的程序员回到老家，发现父亲留下的纸扎铺';
  await typeInto('#ai-idea', idea);
  check('计数跟着输入走',
    (await evaluate(`document.querySelector('#ai-count').textContent`)) === String(idea.length),
    await evaluate(`document.querySelector('#ai-count').textContent`));
  check('「开始创作」变为可点',
    await evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='开始创作' && !b.disabled)`));
});

await step('S5 点「开始创作」→ 真发请求 → 跳向向导路由', async () => {
  await clickText('开始创作');
  await waitFor(`location.hash.indexOf('/playlet/review/p-mock-ai') >= 0`, { label: '跳到 p-mock-ai' });
  check('路由已进入向导页', true, await evaluate('location.hash'));
  // ⚠️ 这条断言**曾经是**"页面显示未移植" —— 向导页移植之后它就过期了。
  //    （本项目纪律：改完功能要复核断言是否还在测同一件事。）
  await waitFor(`!!document.querySelector('.info-card') || !!document.querySelector('.empty')`, { label: '向导页' });
  check('跳到了向导页（不再是"未移植"占位）', await evaluate(`!!document.querySelector('.stepper')`));
  check('界面上已经不存在"还没有移植"这类占位',
    !(await evaluate(`document.body.innerText.includes('还没有移植')`)));
  check('假后端收到 1 次 ai-generate', st.aiCreated === 1, 'aiCreated=' + st.aiCreated);
});

await step('S6 回列表：「我的项目」应有 4 张（新建的那张）', async () => {
  await nav();
  await sleep(1500);
  await waitFor(`document.querySelectorAll('.project-card').length === 4`, { label: '4 张卡' });
  check('列表刷新后包含新建项目', true);
});

await step('S7 「精选项目」：后端答复 0 条 ⇒ 说「暂无」，不许说离线/失败', async () => {
  await clickText('精选项目');
  await waitFor(`document.body.innerText.includes('后端暂无标记为精选的项目')`, { label: '暂无文案' });
  const txt = await evaluate('document.body.innerText');
  check('走「后端确实没有」这一档', true);
  check('**没有**误说成"离线"', !txt.includes('本地离线模式取不到'));
  check('**没有**误说成"拉取失败"', !txt.includes('精选项目拉取失败'));
});

await step('S8 换成 500 ⇒ 同一操作改口成「失败 + 我的项目不受影响」', async () => {
  st.failFeatured = true;
  await clickText('我的项目');
  await sleep(200);
  await clickText('精选项目');
  await waitFor(`document.body.innerText.includes('精选项目拉取失败')`, { label: '失败文案' });
  const txt = await evaluate('document.body.innerText');
  check('说出失败原因（带状态码）', /精选项目拉取失败：500/.test(txt), txt.match(/精选项目拉取失败[^\n]*/)?.[0]);
  check('并说明「我的项目不受影响」', txt.includes('我的项目不受影响'));
  st.failFeatured = false;
  await clickText('我的项目');
  await sleep(300);
});

await step('S9 卡片「⋯」→ 菜单弹出 4 项（门户，不被卡片裁切）', async () => {
  await clickNth('.project-card .icon-btn', 0);
  await waitFor(`!!document.querySelector('.ov-menu')`, { label: '菜单' });
  check('菜单出现', true);
  check('菜单挂在 body 上（门户）',
    await evaluate(`document.querySelector('.ov-menu').parentElement === document.body`));
  check('菜单 4 项', (await evaluate(`document.querySelectorAll('.ov-menu-item').length`)) === 4,
    await evaluate(`[...document.querySelectorAll('.ov-menu-item')].map(e=>e.textContent).join('/')`));
  // ★ 这里的几何判据是**量出来的**：内联版实测菜单底边到 916px 而视口只有 905px，
  //   最后一项落在视口外 ⇒ "看得见、点不着"。旧版 Overlay.menu 带超底上翻逻辑。
  const geo = await evaluate(`(() => {
    const it = [...document.querySelectorAll('.ov-menu-item')].pop();
    const b = it.getBoundingClientRect();
    const t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return { bottom: Math.round(b.bottom), winH: innerHeight, hittable: !!(t && t.closest('.ov-menu-item') === it) };
  })()`);
  check('最后一项在视口内且可命中', geo.bottom <= geo.winH && geo.hittable, JSON.stringify(geo));
});

await step('S10 重命名：弹窗预填 → 全选改写 → 确认 → 卡片名变了', async () => {
  await clickText('重命名', 'div');
  await waitFor(`!!document.querySelector('.ov-input')`, { label: '重命名弹窗' });
  check('弹窗预填原值',
    (await evaluate(`document.querySelector('.ov-input').value`)) === '羊毛毡测试集');
  await clickReal('.ov-input', { settle: 100 });
  await pressKey('a', 'KeyA', 65, 2);          // Ctrl+A
  await send('Input.insertText', { text: '改名后的项目' });
  await sleep(200);
  check('输入框已换成新名字',
    (await evaluate(`document.querySelector('.ov-input').value`)) === '改名后的项目');
  await clickText('确认');
  await waitFor(`document.body.innerText.includes('改名后的项目')`, { label: '卡片改名' });
  check('卡片标题跟着变', true);
  check('假后端收到重命名', st.renamed.some((r) => r.name === '改名后的项目'), JSON.stringify(st.renamed.slice(-1)));
});

await step('S11 删除：⋯ → 删除项目 → 确认 → 卡片少一张', async () => {
  const before = await evaluate(`document.querySelectorAll('.project-card').length`);
  await clickNth('.project-card .icon-btn', 0);
  await waitFor(`!!document.querySelector('.ov-menu')`, { label: '菜单' });
  await clickText('删除项目', 'div');
  await waitFor(`document.body.innerText.includes('删除后无法恢复')`, { label: '删除确认' });
  check('确认弹窗说清后果', true);
  await clickText('删除');
  await waitFor(`document.querySelectorAll('.project-card').length === ${before - 1}`,
    { label: '卡片数 ' + (before - 1) });
  check(`删除生效（${before} → ${before - 1}）`, true);
  check('假后端收到删除', st.deleted.length === 1, JSON.stringify(st.deleted));
});

await step('S12 粘贴剧本 → 弹窗 → 打字 → 完成 → 「已粘贴文本」态', async () => {
  await clearTarget();
  // ⚠️ 别用 clickText('上传我的剧本')：**页面上有两个同名按钮**
  //    （创建区的 tab ＋ 拖拽区里那个），按文字点会点到小的那个。
  await clickNth('.create-tab', 0);
  await sleep(200);
  await clickText('粘贴剧本');
  await waitFor(`!!document.querySelector('.ov-textarea')`, { label: '粘贴弹窗' });
  check('弹窗默认「完成」不可点', await evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='完成'&&b.disabled)`));
  await typeInto('.ov-textarea', '1.内景 纸扎铺 - 夜\n他推开门，白布下的纸人转过脸来。');
  check('输入后计数正确',
    /^[\d]+\/100000$/.test(await evaluate(`document.querySelector('.ov-count').textContent.trim()`)),
    await evaluate(`document.querySelector('.ov-count').textContent.trim()`));
  await clickText('完成');
  await waitFor(`document.body.innerText.includes('已粘贴文本')`, { label: '已粘贴态' });
  check('进入「已粘贴文本」态', true);
  check('出现「重新粘贴」', await evaluate(`document.body.innerText.includes('重新粘贴')`));
  // ★ 风格按钮是**按分组只显示当前类别**的（`.pasted-style` = 当前类别里的包），
  //   所以数量等于类别的包数、不是总数 —— 第一版按"总数 3"断言，误报了一次。
  const g1 = await evaluate(`JSON.stringify({
    cat: document.querySelector('.pasted-cats .asset-tab.is-active').textContent.trim(),
    n: document.querySelectorAll('.pasted-style').length,
    cats: [...document.querySelectorAll('.pasted-cats .asset-tab')].map(e=>e.textContent.trim()),
  })`);
  const g = JSON.parse(g1);
  check('默认类别是第一个分组，且只列该组的包',
    g.cat === '写实' && g.n === 1, g1);
  check('类别页签列全 2 个分组', g.cats.length === 2, JSON.stringify(g.cats));
  check('「剧本解析」在选风格前是禁用的',
    await evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='剧本解析'&&b.disabled)`));
});

await step('S13 换类别 → 选风格 → 「剧本解析」可用 → 点它真的打后端并跳转', async () => {
  // 切到「动画」分组 → 该组的 2 个包应出现（顺带验类别页签真的接线了）
  await clearTarget();
  await evaluate(`(() => {
    const t = [...document.querySelectorAll('.pasted-cats .asset-tab')].find(e => e.textContent.trim() === '动画');
    if (!t) throw new Error('没有「动画」类别页签');
    t.setAttribute('data-e2e-target', '1');
  })()`);
  await clickReal('[data-e2e-target="1"]');
  await dropTarget();
  const n2 = await evaluate(`document.querySelectorAll('.pasted-style').length`);
  check('切到「动画」组 ⇒ 列出该组 2 个包', n2 === 2, 'n=' + n2);
  await clickNth('.pasted-style', 1);        // 点第二个（羊毛毡故事）
  await sleep(250);
  check('选中态上去了',
    (await evaluate(`document.querySelectorAll('.pasted-style.is-active').length`)) === 1);
  const label = await evaluate(`document.querySelector('.pasted-foot .btn .muted').parentElement.textContent.trim()`);
  check('按钮回显已选风格', label.includes('羊毛毡故事'), label);
  check('「剧本解析」已可点',
    await evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='剧本解析'&&!b.disabled)`));
  await clickText('剧本解析');
  await waitFor(`location.hash.indexOf('/playlet/review/p-mock-paste') >= 0`, { label: '跳到 p-mock-paste' });
  check('解析后跳向新项目', true, await evaluate('location.hash'));
  check('假后端收到 paste 请求', st.pasted === 1, 'pasted=' + st.pasted);
});

/* ---------------- 向导页 / 分镜页 / 人工确认条（2026-09-19 补齐） ---------------- */

await step('S15 向导页第 1 步：简介 + 剧本正文（真点卡片进去）', async () => {
  await nav();
  await sleep(1500);
  await waitFor(`document.querySelectorAll('.project-card').length >= 1`, { label: '列表' });
  await clickNth('.project-cover', 0);
  await waitFor(`location.hash.indexOf('/playlet/review/') >= 0`, { label: '进向导' });
  await waitFor(`!!document.querySelector('.info-card')`, { label: '简介卡' });
  check('工作台页**没有**侧栏（与列表页不同壳）', !(await evaluate(`!!document.querySelector('.sider')`)));
  check('顶栏常驻且可见（`shell-top-pinned` 生效）', await evaluate(
    `(() => { const t = document.querySelector('.topbar'); const b = t.getBoundingClientRect();
      return document.body.classList.contains('shell-top-pinned') && Math.round(b.top) === 0; })()`));
  const steps = await evaluate(`[...document.querySelectorAll('.stepper .step')].map(e=>e.textContent.trim())`);
  check('步骤条 3 步齐全', steps.length === 3, JSON.stringify(steps));
  check('简介**跳过了空字段**（只有非空的几行）',
    (await evaluate(`[...document.querySelectorAll('.info-card .info-field .k')].map(e=>e.textContent).join(',')`))
      === '剧集,故事类型,剧情概要,世界观设定,角色设定');
  const sd = await evaluate(`document.querySelectorAll('.sd > *').length`);
  check('剧本正文渲染成结构化文档（不是 markdown 源码）', sd > 5, '块数 ' + sd);
  check('正文里没有 markdown 源码泄漏（`**`）',
    !(await evaluate(`/\\*\\*/.test(document.querySelector('.sd').innerHTML)`)));
  check('底部 sticky 文案正确',
    (await evaluate(`document.querySelector('.sticky-bar .tip').textContent.trim()`)).includes('请确认脚本概要'));

  // ★★ 弹层上的按钮**必须真的有效果**。
  //   为什么单列一条：我自己犯过"处理函数写成空函数 ⇒ 死按钮"的错
  //   （那个「正在处理…」弹窗，两个按钮点了毫无反应，用户以为界面坏了）。
  //   这类 bug 的判据只能是"点完看它有没有真的关掉"，DOM 存在与否证明不了。
  await clearTarget();
  await evaluate(`(() => { const b = document.querySelector('.info-card-head .icon-btn');
    b.setAttribute('data-e2e-target','1'); })()`);
  await clickReal('[data-e2e-target="1"]'); await dropTarget();
  await waitFor(`!!document.querySelector('.ov-mask')`, { label: '编辑简介弹窗' });
  check('「编辑简介」弹层打开', true);
  await clickText('取消');
  const modalGone = await waitFor(`!document.querySelector('.ov-mask')`, { timeout: 3000, label: '弹层关闭' })
    .then(() => true).catch(() => false);
  check('点「取消」弹层**真的关掉**（不是死按钮）', modalGone);
});

await step('S16 向导页第 2/3 步 + 分镜页', async () => {
  // 第 2 步：资产库
  await clearTarget();
  await evaluate(`(() => { const t = [...document.querySelectorAll('.stepper .step')].find(e => e.textContent.includes('资产库'));
    t.setAttribute('data-e2e-target','1'); })()`);
  await clickReal('[data-e2e-target="1"]'); await dropTarget();
  await waitFor(`!!document.querySelector('.asset-grid')`, { label: '资产库' });
  check('资产库 3 个类别页签',
    (await evaluate(`[...document.querySelectorAll('.asset-tab')].map(e=>e.textContent.trim()).join(',')`))
      === '角色列表,场景列表,道具列表');
  // ⚠️ 网格只渲染**当前页签**那一类 ⇒ 默认「角色列表」应是 1 张（不是总数 3）。
  //    （第一版按总数断言，误报了一次 —— 与风格按钮那次同型。）
  check('默认页签只列该类资产（1 张角色卡）',
    (await evaluate(`document.querySelectorAll('.asset-card').length`)) === 1);

  // 切到「场景列表」：验"场景不生图"这条如实说明 + 死控件已拿掉
  await clearTarget();
  await evaluate(`(() => { const t = [...document.querySelectorAll('.asset-tab')].find(e => e.textContent.includes('场景列表'));
    t.setAttribute('data-e2e-target','1'); })()`);
  await clickReal('[data-e2e-target="1"]'); await dropTarget();
  await waitFor(`!!document.querySelector('.placeholder--scene')`, { label: '场景卡' });
  check('★ 场景卡如实说明"不生图"，且**没有**死按钮（不摆点了没反应的控件）',
    await evaluate(`(() => {
      const thumb = document.querySelector('.asset-card .placeholder--scene')?.closest('.asset-thumb');
      return !!thumb && !thumb.querySelector('.asset-refresh');
    })()`));

  // 第 3 步：分集视频
  await clearTarget();
  await evaluate(`(() => { const t = [...document.querySelectorAll('.stepper .step')].find(e => e.textContent.includes('分集视频'));
    t.setAttribute('data-e2e-target','1'); })()`);
  await clickReal('[data-e2e-target="1"]'); await dropTarget();
  await waitFor(`!!document.querySelector('.ep-card')`, { label: '分集卡' });
  check('分集卡渲染', (await evaluate(`document.querySelectorAll('.ep-card').length`)) >= 1);

  // 进分镜页
  await clearTarget();
  await evaluate(`(() => { const b = [...document.querySelectorAll('.ep-card [data-action="ep-menu"]')][0];
    b.setAttribute('data-e2e-target','1'); })()`);
  await clickReal('[data-e2e-target="1"]'); await dropTarget();
  await waitFor(`!!document.querySelector('.ov-menu')`, { label: '剧集菜单' });
  await clickText('打开分镜', 'div');
  await waitFor(`!!document.querySelector('.sb-layout')`, { label: '分镜布局' });
  const sb = await evaluate(`JSON.stringify({
    rail: document.querySelectorAll('.sb-rail-item').length,
    panels: document.querySelectorAll('.sb-panel').length,
    genCards: document.querySelectorAll('.sb-gen-card').length,
    shots: document.querySelectorAll('.sb-shot').length,
    vendors: [...document.querySelectorAll('select[data-vendor]')].map(s => s.getAttribute('data-vendor') + ':' + s.options.length),
    qc: document.querySelectorAll('.qc-chip').length,
    compose: (() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === '生成最终视频'); return b ? b.disabled : 'none'; })(),
    foot: !!document.querySelector('.sb-foot'),
  })`);
  const j = JSON.parse(sb);
  check('缩略轨 / 镜头面板 / 生成卡数对得上（3 镜 → 6 张生成卡）',
    j.rail === 3 && j.panels === 3 && j.genCards === 6 && j.shots === 3, sb);
  check('厂商选择器按后端列（image/video 各 2 项）',
    j.vendors.join(',') === 'image:2,video:2', JSON.stringify(j.vendors));
  check('质检开关 2 个（默认关）', j.qc === 2);
  check('★「生成最终视频」按"有没有视频"判可点（无视频 ⇒ 禁用；旧版这里曾用选中数，是误导性控件）',
    j.compose === true, String(j.compose));
  check('镜次末尾的统一约束作为脚注展示', j.foot);

  // 选择门控：点「全选」后批量按钮解锁
  await clearTarget();
  await evaluate(`(() => { const b = document.querySelector('[data-action="sel-all"], .sb-selchip');
    b.setAttribute('data-e2e-target','1'); })()`);
  await clickReal('[data-e2e-target="1"]'); await dropTarget();
  await sleep(300);
  check('点「全选」后批量生成按钮解锁（选中数 = 镜数）',
    await evaluate(`(() => { const chips = [...document.querySelectorAll('.sb-selchip')];
      return chips.some(c => c.textContent.includes('已选择 3')); })()`));
});

await step('S17 人工确认条：链路挂在等人时，条子出现且能"继续"', async () => {
  st.hitlPending = true;
  // 轮询是 5 秒一次；用 visibilitychange 技巧催一次（实现里"一旦可见立刻查"）
  await evaluate(`(() => { document.dispatchEvent(new Event('visibilitychange')); return 1; })()`);
  const ok = await waitFor(`!!document.querySelector('.hitl-bar')`, { timeout: 12000, label: '确认条出现' })
    .then(() => true).catch(() => false);
  check('链路挂起时确认条出现（跨页存活，挂在 body 上）', ok);
  if (ok) {
    const txt = await evaluate(`document.querySelector('.hitl-bar').textContent`);
    check('说清"刚产出 / 下一步"且角色名译成中文',
      txt.includes('等你确认') && txt.includes('剧本（scriptwriter）') && txt.includes('台词清单（dialogue）'), txt);
    check('预留底部空间（body.has-hitl-bar）',
      await evaluate(`document.body.classList.contains('has-hitl-bar')`));
    check('**只有两个**按钮（刻意不给"中止"）',
      (await evaluate(`document.querySelectorAll('.hitl-bar button').length`)) === 2);
    await clickText('继续');
    await waitFor(`!document.querySelector('.hitl-bar')`, { timeout: 6000, label: '条子收起' });
    check('点「继续」后条子收起', true);
    check('后端收到 approve 决定', st.decisions.some((d) => d.decision === 'approve'),
      JSON.stringify(st.decisions));
  }
});

await step('S14 清账：无未实现请求、无重复水合循环、无控制台报错', async () => {
  const notImpl = st.log.filter((e) => e.status === 404);
  check('没有落到"假后端未实现"的 404', notImpl.length === 0,
    notImpl.map((e) => e.method + ' ' + e.path).join(' | '));
  // ⚠️ 过滤条件必须**精确到 `is_demo=false`**：第一版写成
  //   `startsWith('.../projects?page=1&page_size=100')`，于是「精选项目」
  //   那两次（`is_demo=true`）也被算了进来，凭空多出 2 条"无导航的水合"，
  //   差点当成循环去查。**断言写松了会误报失败**（与"误判成功"一样要防）。
  const listHits = st.log.filter((e) => e.path.endsWith('is_demo=false'));
  // 无环判据：**相邻两次请求间隔 ≥1s**（"空闲时还在刷"或"越点越多"才是问题）。
  // 别用绝对次数当判据 —— 它会被"这一轮导航了几次"污染（第一轮就误报过一次）。
  const gaps = listHits.map((e, i) => (i ? e.at - listHits[i - 1].at : 0));
  check('列表水合没有连环请求（不是循环）', gaps.every((g) => g === 0 || g >= 1000),
    '共 ' + listHits.length + ' 次，间隔(ms) ' + gaps.join('/'));
  const hydrLog = JSON.parse(await evaluate(
    `sessionStorage.getItem('__hydrations') || '[]'`,
  )).map((h) => h.key);
  const listHydr = hydrLog.filter((k) => k.startsWith('/playlet/list')).length;
  check('每次进列表只水合一次（水合次数 = 列表导航次数）', listHydr === listHits.length,
    '埋点 ' + listHydr + ' 次 vs 请求 ' + listHits.length + ' 次；全部埋点 key = ' + JSON.stringify(hydrLog));
  check('页面无异常 / 无 console.error', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));
  check('__APPERR 始终为空', (await evaluate('(window.__APPERR||[]).length')) === 0);
  console.log('\n  请求流水（' + st.log.length + ' 条）：');
  st.log.slice(-14).forEach((e) => console.log(`    ${e.status || '?'}  ${e.method} ${e.path}`));
});

/* ------------------------------------------------------------------ 收尾 */

await nav();
await sleep(1500);
await evaluate(`document.querySelector('.project-card') && document.querySelector('.project-card').scrollIntoView({block:'center'});1`);
await sleep(400);
const shotPath = process.env.SHOT_PATH
  || 'C:\\Users\\Administrator\\AppData\\Local\\Temp\\sd-shots\\react-mock-e2e.png';
const cap = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
const fs = await import('node:fs');
fs.writeFileSync(shotPath, Buffer.from(cap.data, 'base64'));
console.log('\n截图：' + shotPath);

const failed = results.filter((r) => !r.ok);
console.log('\n================ 结果 ================');
console.log(`共 ${results.length} 条断言，通过 ${results.length - failed.length}，失败 ${failed.length}`);
if (failed.length) {
  console.log('失败清单：');
  failed.forEach((f) => console.log('  ✗ ' + f.name + (f.detail ? '  — ' + f.detail : '')));
}
ws.close();
process.exit(failed.length ? 1 : 0);
