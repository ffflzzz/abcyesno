// 把 BrowserPanel.jsx（真实源码）打包成可在 Node 里直接驱动的 ESM：
// react → mini-react 钩子；react/jsx-runtime → 最小 JSX 运行时。
// 用法：node scripts/test-browser-panel/build.mjs [源文件] [输出文件]
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const src = path.resolve(root, process.argv[2] || "src/components/BrowserPanel.jsx");
const out = path.resolve(root, process.argv[3] || "tmp/bp-browser-panel.mjs");

// mini-react 必须与用例共用同一实例：否则组件（被打包的那份）与 mount()（用例
// 导入的那份）各持一套 hooks，效果不执行、setState 触发空 renderFn。
// env.mjs 同理：假的 <webview> 节点与 env.pageUrl 必须是同一对象，用例才改得动。
const externalize = (abs) => ({ path: pathToFileURL(abs).href, external: true });
const envUrl = pathToFileURL(path.join(here, "env.mjs")).href;

await esbuild.build({
  entryPoints: [src],
  outfile: out,
  bundle: true,
  format: "esm",
  platform: "node",
  jsx: "automatic",
  logLevel: "warning",
  plugins: [
    {
      name: "fake-react",
      setup(build) {
        build.onResolve({ filter: /mini-react\.mjs$/ }, () => externalize(
          path.resolve(here, "..", "test-multisession", "mini-react.mjs"),
        ));
        build.onResolve({ filter: /(^|\/|\\\\)env\.mjs$/ }, () => ({ path: envUrl, external: true }));
        build.onResolve({ filter: /^react$/ }, () => ({
          path: path.join(here, "react-hooks.mjs"),
        }));
        build.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({
          path: path.join(here, "jsx-runtime.mjs"),
        }));
      },
    },
  ],
});

console.log(`bundled ${path.relative(root, src)} -> ${path.relative(root, out)}`);
