/**
 * 右栏"和导演对话"的浏览器验收：真打字、真发送、真等他回。
 *
 * 验的是这一版的新东西 —— 类型检查证明不了它们：
 *   ① 右栏是不是**真对话**（发一句 → 时间线里出现他的回答）
 *   ② 「开工」按钮在没聊过时是灰的（⛔ 不许空点开工）
 *   ③ 危险按钮（开工 / 打回 / 中止 / 让导演重做）都带 data-danger，脚本一律不点
 *
 * 跑法（后端自己起一个，别占别人的口）：
 *   SHIM_PORT=8791 PW_CHROMIUM=<chrome> node shortdrama/scripts/studio_chat_shot.mjs <项目名>
 * ⛔ 只对**临时项目**跑（它会真的花一次文本额度）。
 */
import { chromium } from 'playwright-core';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });

const PID = process.argv[2] || 'chatprobe-1007';
const PORT = process.env.SHIM_PORT || '8791';
const URL = `http://127.0.0.1:${PORT}/studio/`;

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.setDefaultTimeout(8000);
const errors = [];
page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e.message).slice(0, 160)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)); });

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(5000);

const target = page.locator('.rail-item', { hasText: PID }).first();
if (await target.count()) { await target.click(); console.log('已点选', PID); }
else console.log('!! 左栏找不到', PID);
await page.waitForTimeout(3000);
await page.screenshot({ path: path.join(OUT, 'chat-01-open.png') });

// ① 开工按钮在还没说话时必须是灰的
const startBtn = page.locator('.chat-input button', { hasText: '开工' }).first();
const before = await startBtn.isDisabled().catch(() => null);
console.log('还没聊过时「开工」是灰的 =', before, before === true ? 'PASS' : '!! 应该灰着');

const dangers = await page.locator('[data-danger]').count();
console.log(`标了 data-danger 的危险按钮 ${dangers} 个 —— 本脚本一律不点`);

// ② 真说一句
const n0 = await page.locator('.msg').count();
const ta = page.locator('.chat-input textarea').first();
await ta.fill('用一句话说：这条片子你最担心哪个环节？');
await page.locator('.chat-input button:not([data-danger])', { hasText: '发送' }).first().click();
console.log('已发送，等导演回…');

let replied = false;
for (let i = 0; i < 60; i += 1) {              // 最多等 3 分钟
  await page.waitForTimeout(3000);
  const n = await page.locator('.msg').count();
  const busy = (await page.locator('.chat-body .spin').count()) > 0;
  if (i % 4 === 0) console.log(`   ${i * 3}s: 气泡 ${n} 个（跑前 ${n0}）busy=${busy}`);
  if (!busy && n >= n0 + 2) { replied = true; break; }
}
const last = await page.locator('.msg--sys').last().innerText().catch(() => '');
console.log(replied ? 'PASS 导演回了话' : '!! 没等到回答');
console.log('他的回答（截断）:', JSON.stringify(last.slice(0, 200)));

// ③ 聊过之后开工按钮应该可点（但**本脚本不点**）
const after = await startBtn.isDisabled().catch(() => null);
console.log('聊过之后「开工」可点 =', after === false, after === false ? 'PASS' : '!! 仍灰着');

await page.screenshot({ path: path.join(OUT, 'chat-02-replied.png') });
console.log('\n控制台 error 数 =', errors.length);
[...new Set(errors)].slice(0, 6).forEach((e) => console.log('  ·', e));
console.log('截图：', OUT);
await browser.close();
