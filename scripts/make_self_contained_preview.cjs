// Self-contained bundler for dev preview. Inlines the entire vite dist
// into a single HTML file so:
//   - file:// loads work (no cross-origin fetch)
//   - preview panels that wrap us in a sandboxed iframe work
//   - http://127.0.0.1:4173/ loads still work
//
// Total inlined ~920 KB; small enough for a one-off preview.

const fs = require("fs");
const path = require("path");

const PREVIEW = path.resolve(__dirname, "..", "dist-preview");
const assets = path.join(PREVIEW, "assets");

const jsName = fs.readdirSync(assets).find((n) => /^index-.*\.js$/.test(n));
const cssName = fs.readdirSync(assets).find((n) => /^index-.*\.css$/.test(n));
if (!jsName || !cssName) {
  console.error("index-*.js / index-*.css not found in dist-preview/assets/");
  process.exit(1);
}

const js = fs.readFileSync(path.join(assets, jsName), "utf8");
const css = fs.readFileSync(path.join(assets, cssName), "utf8");

// Inline module/style content must not contain a literal closing tag,
// or it would terminate the surrounding <script>/<style> early.
const safeJs = js.split("</script>").join("<\\/script>");
const safeCss = css.split("</style>").join("<\\/style>");

// Bundle references launcher/icon PNGs via new URL("NAME.png",import.meta.url).
// Inline them as data URIs so the page is fully self-contained (works under
// file://, http://, and the sandboxed preview panel with zero external fetches).
const assetFiles = fs
  .readdirSync(assets)
  .filter((n) => /\.(png|svg|jpe?g|webp|gif|woff2?)$/i.test(n));

let safeJsWithAssets = safeJs;
let inlinedKb = 0;
for (const name of assetFiles) {
  const marker = `new URL("${name}",import.meta.url)`;
  if (!safeJsWithAssets.includes(marker)) continue;
  const b64 = fs.readFileSync(path.join(assets, name)).toString("base64");
  const ext = path.extname(name).slice(1).toLowerCase();
  const mime = ext === "svg" ? "image/svg+xml" : ext === "woff2" ? "font/woff2"
    : ext.startsWith("woff") ? "font/woff" : `image/${ext === "jpg" ? "jpeg" : ext}`;
  const dataUri = `data:${mime};base64,${b64}`;
  safeJsWithAssets = safeJsWithAssets.split(marker).join(JSON.stringify(dataUri));
  inlinedKb += b64.length / 1024;
}

const html = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Abcyesno</title>
    <!-- DEV PREVIEW MOCK — fake window.hermes so the React tree can
         hydrate in a plain browser without booting Electron + Hermes.
         Inert in production because the real Electron preload injects
         the genuine bridge before main.jsx executes. -->
    <script>
      (function () {
        if (window.hermes) return;
        const ok = (v) => Promise.resolve(v);
        const unsub = () => () => {};
        const noop = () => {};
        // Known IPC surface (Electron preload normally injects the real bridge).
        const known = {
          getAguiPort: () => ok(9999),
          onAguiReady: (cb) => { try { cb(); } catch (_) {} return unsub; },
          offAguiReady: noop,
          openDevTools: noop,
          quitApp: noop,
          openDataDir: noop,
          openExternal: () => ok(),
          onOpenSettings: () => unsub,
          offOpenSettings: noop,
          validateApiKey: () => ok(true),
          onApiKeySaved: () => unsub,
          offApiKeySaved: noop,
          uploadFile: () => ok({ path: '' }),
          selectFile: () => ok(null),
          transcribeAudio: () => ok(''),
          readFile: () => ok(''),
          readLocalImage: () => ok(null),
          interruptSession: () => ok({ interrupted: true }),
          listWorkspace: () => ok([]),
          getBrowserInfo: () => ok(null),
          clearBrowserWebview: () => ok(),
          reportBrowserWebview: () => ok(),
          paperDownload: () => ok(),
          studioCall: () => ok({ ok: true, data: null }),
          parseWorkflowIntent: () => ok({ intent: 'free', items: [] }),
          sendWorkflowInterrupt: () => ok({ ok: true }),
          wechatCall: () => ok({ ok: true, data: { state: 'idle' } }),
          onWechatStatus: () => () => {},
          on: () => () => {},
          off: () => {},
        };
        // Proxy fallback: any unlisted method returns a safe Promise/noop so the
        // React tree can hydrate without throwing "not a function" in a browser.
        window.hermes = new Proxy(known, {
          get(t, prop) {
            if (prop in t) return t[prop];
            return () => ok(undefined);
          },
        });
      })();
    </script>
    <style data-origin="${cssName}">
${safeCss}
    </style>
  </head>
  <body>
    <div id="root"></div>
    <!-- Visible "this is a mock" badge so screenshots are unambiguous. -->
    <div style="position:fixed;bottom:8px;right:8px;padding:4px 10px;background:#d1242f;color:#fff;font-size:11px;font-weight:500;border-radius:6px;z-index:9999;pointer-events:none;font-family:system-ui, sans-serif;">DEV PREVIEW · mock hermes · agui unreachable</div>
    <script type="module" data-origin="${jsName}">
${safeJsWithAssets}
    </script>
  </body>
</html>
`;

fs.writeFileSync(path.join(PREVIEW, "index.html"), html, "utf8");

// Clean up — single file only, no external dependencies to fetch.
fs.rmSync(path.join(assets, jsName), { force: true });
fs.rmSync(path.join(assets, cssName), { force: true });
const mapName = `${jsName}.map`;
fs.rmSync(path.join(assets, mapName), { force: true });

console.log(
  `self-contained preview written: dist-preview/index.html ` +
  `(css+js inlined, ${((js.length + css.length) / 1024 | 0)} KB; ` +
  `${assetFiles.length} asset(s) referenced, ${inlinedKb | 0} KB data-URI'd)`
);
