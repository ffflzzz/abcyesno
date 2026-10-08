/**
 * 打开即白纸：不自动钻历史项目；右栏先「开一段新对话」。
 *   SHIM_PORT=8797 PW_CHROMIUM=<chrome> node shortdrama/scripts/studio_open_shot.mjs
 */
import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';

const OUT = path.join('shortdrama', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });
const PORT = process.env.SHIM_PORT || '8797';

const b = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 1585, height: 918 } });
p.setDefaultTimeout(10000);
const errs = [];
p.on('pageerror', (e) => errs.push(String(e.message).slice(0, 140)));

await p.goto(`http://127.0.0.1:${PORT}/studio/`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(5000);

// ① 打开时**没有**自动选中任何项目
const on = await p.locator('.rail-item--on').count();
console.log('① 打开时被选中的项目数 =', on, on === 0 ? 'PASS 没自动钻' : '!! 还是自动选了');

// ② 中栏是那块能画的画布（不是"到左栏选一个项目"）
const src = await p.locator('.stage-canvas iframe').first().getAttribute('src').catch(() => '(无 iframe)');
const hint = await p.locator('.stage-hint').first().innerText().catch(() => '(无)');
console.log('② 中栏 iframe =', src);
console.log('   左下提示 =', JSON.stringify(hint.replace(/\s+/g, ' ')));

// ③ 右栏给的是「开始新对话」
const hero = await p.locator('.chat-hero').first().innerText().catch(() => '(无)');
const taDisabled = await p.locator('.chat-input textarea').first().isDisabled();
console.log('③ 右栏空态 =', JSON.stringify(hero.replace(/\s+/g, ' ')));
console.log('   输入框禁用 =', taDisabled, taDisabled ? '（要先开对话）PASS' : '!! 不该可用');
await p.screenshot({ path: path.join(OUT, 'open-01-blank.png') });

// ④ 点「开始新对话」→ 建空白项目 → 选中 → 输入框可用
await p.locator('.chat-hero button', { hasText: '开始新对话' }).first().click();
await p.waitForTimeout(6000);
const on2 = await p.locator('.rail-item--on').count();
const taDisabled2 = await p.locator('.chat-input textarea').first().isDisabled();
console.log('④ 点完之后：选中的项目数 =', on2, '| 输入框禁用 =', taDisabled2,
  (on2 === 1 && !taDisabled2) ? 'PASS 对话可用了' : '!! 没接上');
console.log('   左栏第一个项目 =', await p.locator('.rail-item').first().innerText().catch(() => '(无)'));
await p.screenshot({ path: path.join(OUT, 'open-02-newchat.png') });

console.log('\npageerror =', errs.length, errs.slice(0, 3));
await b.close();
