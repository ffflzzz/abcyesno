/**
 * 四条改动的浏览器验收：
 *   ① 「旧工作台」没了   ② 导演回答渲染成 markdown（不再是一堆 ** 和 ##）
 *   ③ 滚动条不再是白的   ④ 左右两栏能拖宽、能收起、刷新生效
 *
 * SHIM_PORT=8788 PW_CHROMIUM=<chrome> node scripts/studio_fix_shot.mjs <项目名>
 */
import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';

const OUT = path.join('shortdrama', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });
const PID = process.argv[2] || 'drydock-dawn-1003';
const PORT = process.env.SHIM_PORT || '8788';

const b = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 1585, height: 918 } });
p.setDefaultTimeout(8000);
const errs = [];
p.on('pageerror', (e) => errs.push(String(e.message).slice(0, 140)));

await p.goto(`http://127.0.0.1:${PORT}/studio/`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(5000);
const t = p.locator('.rail-item', { hasText: PID }).first();
if (await t.count()) await t.click();
await p.waitForTimeout(6000);

const grid = () => p.evaluate(() => getComputedStyle(document.querySelector('.studio')).gridTemplateColumns);

// ① 旧工作台
const oldLink = await p.locator('text=旧工作台').count();
console.log('① 「旧工作台」出现次数 =', oldLink, oldLink === 0 ? 'PASS 已去掉' : '!! 还在');

// ② markdown
const sysText = (await p.locator('.msg--sys').last().innerText().catch(() => '')).slice(0, 400);
const starCount = (sysText.match(/\*\*/g) || []).length;
const hasH = await p.locator('.msg--sys .md h4, .msg--sys .md h5').count();
const hasList = await p.locator('.msg--sys .md ul li, .msg--sys .md ol li').count();
console.log(`② 导演那条里残留的 ** =${starCount}  渲染出的标题 =${hasH}  列表项 =${hasList}`);
console.log(starCount === 0 && (hasH > 0 || hasList > 0) ? 'PASS 渲染成富文本了' : '!! 还是原文');

// ③ 滚动条配色
const scheme = await p.evaluate(() => getComputedStyle(document.documentElement).colorScheme);
console.log('③ html color-scheme =', scheme, scheme.includes('dark') ? 'PASS' : '!! 没吃到');

// ④ 拖拽 + 收起
const before = await grid();
console.log('④ 拖前 grid =', before);
const h = p.locator('.rail .split').first();
const box = await h.boundingBox();
await p.mouse.move(box.x + box.width / 2, box.y + 300);
await p.mouse.down();
await p.mouse.move(box.x + box.width / 2 + 120, box.y + 300, { steps: 12 });
await p.mouse.up();
await p.waitForTimeout(500);
const after = await grid();
console.log('   拖后 grid =', after, before !== after ? 'PASS 宽度变了' : '!! 没变');
const persisted = await p.evaluate(() => localStorage.getItem('sd.railW'));
console.log('   记进本地 =', persisted);

await p.locator('.rail .panel-x').first().click();
await p.waitForTimeout(600);
const collapsed = await p.evaluate(() => document.querySelector('.studio').className);
const reopen = await p.locator('.reopen--left').count();
console.log('   收起后 class =', collapsed, '| 重开按钮 =', reopen, collapsed.includes('no-rail') && reopen ? 'PASS' : '!! 收起没生效');
await p.screenshot({ path: path.join(OUT, 'fix-01-collapsed.png') });

await p.locator('.reopen--left').click();
await p.waitForTimeout(500);
await p.screenshot({ path: path.join(OUT, 'fix-02-open.png') });

// 刷新后宽度还在吗
await p.reload({ waitUntil: 'domcontentloaded' });
await p.waitForTimeout(4000);
console.log('   刷新后 grid =', await grid(), '(应与拖后的值一致)');

console.log('\npageerror =', errs.length, errs.slice(0, 3));
console.log('截图:', OUT);
await b.close();
