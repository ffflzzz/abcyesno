/**
 * 空白项目 → 中栏给的是**一块能画的空画布**（而不是一句只读提示）。
 *   SHIM_PORT=8794 PW_CHROMIUM=<chrome> node shortdrama/scripts/studio_blank_shot.mjs
 */
import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';

const OUT = path.join('shortdrama', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });
const PORT = process.env.SHIM_PORT || '8794';
const b = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 1585, height: 918 } });
p.setDefaultTimeout(10000);

const errs = [];
p.on('pageerror', (e) => errs.push(String(e.message).slice(0, 140)));

await p.goto(`http://127.0.0.1:${PORT}/studio/`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(4000);

// 建一个空白项目（走真接口，**不调模型**）
const r = await p.evaluate(async (port) => {
  const res = await fetch(`http://127.0.0.1:${port}/v1/pixa/short-drama/projects/blank`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: '空画布试一下', episodes: 1, ratio: '9:16' }),
  });
  return res.json();
}, PORT);
const pid = (r.data || {}).pid;
console.log('建了空白项目 =', pid, JSON.stringify(r.data || r.message));

await p.reload({ waitUntil: 'domcontentloaded' });
await p.waitForTimeout(4000);
const item = p.locator('.rail-item', { hasText: pid }).first();
if (await item.count()) await item.click();
await p.waitForTimeout(5000);

console.log('中栏 iframe 数 =', await p.locator('.stage-canvas iframe').count());
console.log('画布指向 =', await p.locator('.stage-canvas iframe').first().getAttribute('src').catch(() => '(无)'));
const hint = await p.locator('.stage-hint').innerText().catch(() => '(无提示)');
console.log('左下提示 =', JSON.stringify(hint.replace(/\s+/g, ' ')));
console.log('提示里还有没有变量名（不该有）=', /stills\.json|SHORTDRAMA_/.test(hint));
await p.screenshot({ path: path.join(OUT, 'blank-01.png') });
console.log('pageerror =', errs.length, errs.slice(0, 2));
console.log('截图:', path.join(OUT, 'blank-01.png'));
await b.close();
