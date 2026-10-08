/**
 * 「直接进画布」验收：中栏不该停在**画布库**（第一张截图），
 * 而应落在**画布编辑页**（第二张截图）。
 *
 *   SHIM_PORT=8787 PW_CHROMIUM=<chrome> node shortdrama/scripts/studio_gocanvas_shot.mjs
 *
 * 只读盘上事实 + 只开新标签，不点任何破坏性按钮。
 */
import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';

const OUT = path.join('shortdrama', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });
const PORT = process.env.SHIM_PORT || '8787';
const BASE = `http://127.0.0.1:${PORT}`;

const b = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] });
const errs = [];

// ── ① 直挂参数：/atelier/canvas?go=last 应当落到 /atelier/canvas/<id>
const p1 = await b.newPage({ viewport: { width: 1585, height: 918 } });
p1.setDefaultTimeout(15000);
p1.on('pageerror', (e) => errs.push('[go=last] ' + String(e.message).slice(0, 140)));
await p1.goto(`${BASE}/atelier/canvas?go=last`, { waitUntil: 'domcontentloaded' });
await p1.waitForTimeout(4000);
const url1 = p1.url();
const landed = /\/atelier\/canvas\/[^/?#]+/.test(url1);
console.log('① /atelier/canvas?go=last →', url1);
console.log('  ', landed ? 'PASS 已在画布编辑页' : '!! 还停在库页');
await p1.screenshot({ path: path.join(OUT, 'go-01-direct.png') });

// 编辑页的标志：画布工具条上有「文本」按钮；库页的标志：卡片列表 +「新建画布」
const libMark = await p1.getByText('画布库').count();
const editorMark = await p1.locator('.react-flow').count();
console.log('   库页标题出现 =', libMark, '| 画布编辑器出现 =', editorMark);

// ── ② 不带参数时行为不变（仍停在库页）
const p2 = await b.newPage({ viewport: { width: 1585, height: 918 } });
p2.setDefaultTimeout(15000);
p2.on('pageerror', (e) => errs.push('[无参] ' + String(e.message).slice(0, 140)));
await p2.goto(`${BASE}/atelier/canvas`, { waitUntil: 'domcontentloaded' });
await p2.waitForTimeout(4000);
const stayed = /\/atelier\/canvas\/[^/?#]+/.test(p2.url()) ? '!! 被带着跳了' : 'PASS 停在库页（未传参 = 一字不变）';
console.log('② /atelier/canvas（不带参数）→', p2.url(), '|', stayed);

// ── ③ 工作室整页：中栏 iframe 的 src 与它实际渲染出来的页面
const p3 = await b.newPage({ viewport: { width: 1585, height: 918 } });
p3.setDefaultTimeout(20000);
p3.on('pageerror', (e) => errs.push('[studio] ' + String(e.message).slice(0, 140)));
await p3.goto(`${BASE}/studio/`, { waitUntil: 'domcontentloaded' });
await p3.waitForTimeout(7000);
const frames = p3.frames();
console.log('③ studio 里的 frame：');
for (const f of frames) console.log('   -', f.url());
const canvasFrame = frames.find((f) => /\/atelier\/canvas\/[^/?#]+/.test(f.url()));
console.log('   ', canvasFrame ? 'PASS 中栏画布在编辑页' : '!! 中栏没落在编辑页');
await p3.screenshot({ path: path.join(OUT, 'go-02-studio.png') });

console.log('\nJS 错误：', errs.length ? errs : '（无）');
await b.close();
