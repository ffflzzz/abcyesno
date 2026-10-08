/**
 * 画布"看不清"到底是谁的问题：量三个数。
 *   ① iframe 的 CSS 尺寸与实际渲染的 devicePixelRatio
 *   ② 画布当前的缩放百分比（atelier 左下角那个读数）
 *   ③ 一个节点在屏幕上的真实像素宽高 + 里面正文字号
 *
 *   SHIM_PORT=8788 PW_CHROMIUM=<chrome> node shortdrama/scripts/studio_canvas_dpi_shot.mjs
 * 只读 + 只点项目条目，不点任何危险按钮。
 */
import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs';

const OUT = path.join('shortdrama', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });
const PORT = process.env.SHIM_PORT || '8788';
const PID = process.env.PROBE_PID || 'drydock-dawn-1003';

const b = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] });
// 1920x1080 / DPR 1 —— 和他那块屏一致
const p = await b.newPage({ viewport: { width: 1920, height: 1040 }, deviceScaleFactor: 1 });
p.setDefaultTimeout(20000);
const errs = [];
p.on('pageerror', (e) => errs.push(String(e.message).slice(0, 140)));

await p.goto(`http://127.0.0.1:${PORT}/studio/`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(3000);
await p.getByText(PID, { exact: false }).first().click().catch(() => console.log('!! 没点到项目条目'));
await p.waitForTimeout(9000);

const f = p.frames().find((x) => /\/atelier\/canvas\//.test(x.url()));
console.log('frame =', f ? f.url() : '(没找到画布 frame)');

if (f) {
    const info = await f.evaluate(() => {
        const r = document.querySelector('.react-flow');
        const vp = document.querySelector('.react-flow__viewport');
        const node = document.querySelector('.react-flow__node');
        const box = node ? node.getBoundingClientRect() : null;
        const txt = node ? node.querySelector('p,span,div') : null;
        const zoomText = [...document.querySelectorAll('*')]
            .map((e) => (e.children.length === 0 ? e.textContent.trim() : ''))
            .filter((s) => /^\d{1,3}%$/.test(s)).slice(0, 3);
        return {
            dpr: window.devicePixelRatio,
            innerW: window.innerWidth, innerH: window.innerHeight,
            flowBox: r ? { w: Math.round(r.getBoundingClientRect().width), h: Math.round(r.getBoundingClientRect().height) } : null,
            viewportTransform: vp ? getComputedStyle(vp).transform : null,
            nodeCount: document.querySelectorAll('.react-flow__node').length,
            nodeBox: box ? { w: Math.round(box.width), h: Math.round(box.height) } : null,
            nodeFontPx: txt ? getComputedStyle(txt).fontSize : null,
            nodeText: txt ? (txt.textContent || '').trim().slice(0, 24) : null,
            zoomText,
        };
    });
    console.log('① devicePixelRatio =', info.dpr, '| iframe 视口 =', info.innerW + 'x' + info.innerH);
    console.log('② 画布读数 =', JSON.stringify(info.zoomText), '| viewport transform =', info.viewportTransform);
    console.log('③ 节点数 =', info.nodeCount, '| 单节点屏幕尺寸 =', JSON.stringify(info.nodeBox),
        '| 正文字号 =', info.nodeFontPx, '| 文本 =', JSON.stringify(info.nodeText));
    console.log('   .react-flow 尺寸 =', JSON.stringify(info.flowBox));
}

const ifr = await p.locator('.stage-canvas iframe').first().boundingBox().catch(() => null);
console.log('中栏 iframe 在窗口里的尺寸 =', ifr ? Math.round(ifr.width) + 'x' + Math.round(ifr.height) : '(无)');
await p.screenshot({ path: path.join(OUT, 'dpi-01.png') });
if (ifr) await p.screenshot({ path: path.join(OUT, 'dpi-02-canvas.png'), clip: ifr });
console.log('JS 错误：', errs.length ? errs : '（无）');
await b.close();
