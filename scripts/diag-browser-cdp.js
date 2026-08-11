// 浏览器自动化 CDP 可达性诊断（route B 真机验证用）
// 用法：先启动 Abcyesno（确保 remote-debugging-port=18922 已开），
//       打开浏览器面板（让它加载 marker），然后在本项目根目录跑：
//       node scripts/diag-browser-cdp.js
// 目的：确认 Electron 的 <webview> 是否被 CDP 暴露为 type:"webview" 的 target，
//       以及 marker 页面是否能被 Playwright connect_over_cdp 找到。

const http = require('http');
const PORT = process.env.PW_CDP_PORT || 18922;
const base = `http://127.0.0.1:${PORT}`;

function get(path) {
  return new Promise((resolve, reject) => {
    http
      .get(base + path, (res) => {
        let d = '';
        res.on('data', (c) => (d += c));
        res.on('end', () => {
          try {
            resolve(JSON.parse(d));
          } catch (e) {
            resolve(d);
          }
        });
      })
      .on('error', reject);
  });
}

(async () => {
  try {
    let version;
    try {
      version = await get('/json/version');
    } catch (e) {
      console.error(`✗ 无法连接 CDP ${base} —— app 是否在运行且 remote-debugging-port 已开？\n  ${e.message}`);
      process.exit(1);
    }
    console.log('CDP Browser:', version.Browser, '| Protocol:', version.ProtocolVersion);

    const targets = await get('/json');
    console.log(`\n发现 ${targets.length} 个 CDP target：\n`);
    for (const t of targets) {
      const isMarker = (t.url || '').includes('browser-pw-marker');
      const tag = isMarker ? '  <<< MARKER WEBVIEW (BrowserPanel)' : '';
      console.log(`  [${t.type}]${tag}\n      ${t.url || t.webSocketDebuggerUrl || ''}`);
    }

    const webviews = targets.filter((t) => t.type === 'webview');
    const markerWv = targets.filter((t) => (t.url || '').includes('browser-pw-marker'));
    const pages = targets.filter((t) => t.type === 'page');

    console.log('\n--- 诊断 ---');
    console.log('  type:"page"   数量:', pages.length, '(Electron 主窗口在此)');
    console.log('  type:"webview" 数量:', webviews.length, '(BrowserPanel 的 <webview> 应在此)');
    console.log('  marker webview 数量:', markerWv.length);

    if (markerWv.length === 0) {
      console.log('\n❌ CDP 看不到 marker webview。');
      console.log('   Playwright connect_over_cdp 因此找不到 BrowserPanel 的 <webview>，');
      console.log('   → 打包版会报 "browser panel not found"；');
      console.log('   → dev 版可能误选 Electron 主窗口(React app)作为操作目标（返回结果但界面不可观察）。');
      console.log('   根因：Electron 的 <webview> guest 在 CDP 里是 type:"webview"，');
      console.log('         Playwright 的 chromium connectOverCDP 不把它暴露为可操作 page。');
      console.log('   修复方向：改用 Electron 主进程原生驱动 webview（webContents.debugger / executeJavaScript），绕开 Playwright+CDP 的兼容坑。');
    } else {
      console.log('\n✅ marker webview 可被 CDP 访问 —— route B 理论上可用，问题在别处（时机/可见性/前端自动打开）。');
    }
  } catch (e) {
    console.error('诊断失败:', e.message);
    process.exit(1);
  }
})();
