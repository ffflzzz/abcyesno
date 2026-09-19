// BrowserPanel 地址栏回归测试（无浏览器环境，驱动真实组件源码）。
//
// 覆盖 2026-09-19「地址栏无法输入、输入被秒覆盖」的两个根因：
//  ① 用户输入被写进 url（轮询的比较基准）→ 轮询误判"页面跳转了"并回灌 getURL()
//  ② 焦点保护读闭包里的 addressFocused，而 effect 依赖只有 [marker] → 保护失效
//
// 断言链：marker 长串不外显 → 输入不被覆盖 → 回车真的导航 → 落地后回填权威地址。
import path from "node:path";
import { fileURLToPath } from "node:url";
import { env, installHost, resetEnv, settle, webviewNode } from "./env.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const bundle = path.join(root, "tmp", process.env.BP_BUNDLE || "bp-browser-panel.mjs");

const MARKER =
  "data:text/html;charset=utf-8,%3C!doctype%20html%3E%3Ctitle%3Ebrowser-pw-marker%3C%2Ftitle%3E";

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
  if (cond) {
    pass += 1;
    console.log(`  ok  ${name}`);
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
    console.log(`FAIL  ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

function find(node, pred) {
  if (!node || typeof node !== "object") return null;
  if (pred(node)) return node;
  const kids = node.props ? node.props.children : null;
  const list = Array.isArray(kids) ? kids : kids == null ? [] : [kids];
  for (const k of list) {
    const hit = find(k, pred);
    if (hit) return hit;
  }
  return null;
}

const isAddress = (n) => n.type === "input" && n.props && n.props.className === "bt-address";

installHost({ marker: MARKER });
resetEnv();

const miniReact = await import("../test-multisession/mini-react.mjs");
const mod = await import(`file://${bundle.replace(/\\/g, "/")}`);
const BrowserPanel = mod.default;

miniReact.mount(() => BrowserPanel({ progress: [], initialUrl: "" }));
await settle();

const addressOf = () => {
  const node = find(miniReact.getResult(), isAddress);
  if (!node) throw new Error("地址栏 input 未渲染 —— 组件结构可能已变");
  return node;
};

console.log("BrowserPanel 地址栏回归");
check("地址栏不显示内部 marker 长串（data: 占位隐藏）", addressOf().props.value === "");

// 1) 聚焦 → 输入：地址栏必须保留用户输入
addressOf().props.onFocus({ target: { select() {} } });
await settle();
addressOf().props.onChange({ target: { value: "example.com" } });
await settle();
check("输入后地址栏保留用户文本", addressOf().props.value === "example.com",
  `实际 "${addressOf().props.value}"`);

// 2) 关键回归点：跨过 1 Hz 轮询一拍（此时页面仍停在 marker），输入不得被回灌覆盖
await settle(1200);
check("1 Hz 轮询一拍后输入仍未被覆盖", addressOf().props.value === "example.com",
  `实际 "${addressOf().props.value}"`);

// 3) 回车必须按用户输入的地址导航，而不是 marker / 空串
addressOf().props.onKeyDown({ key: "Enter", preventDefault() {} });
await settle();
check("回车导航到用户输入的目标", env.loaded[env.loaded.length - 1] === "https://example.com",
  `实际 "${env.loaded[env.loaded.length - 1]}"`);

// 4) 页面落地（did-navigate）后地址栏回填权威地址并丢弃草稿
env.pageUrl = "https://example.com/home";
webviewNode().fire("did-navigate");
await settle();
check("落地后回填权威地址", addressOf().props.value === "https://example.com/home",
  `实际 "${addressOf().props.value}"`);

// 5) 编辑中遇到 Agent 驱动跳转：不得抢掉正在输入的内容
addressOf().props.onFocus({ target: { select() {} } });
await settle();
addressOf().props.onChange({ target: { value: "半截输入" } });
await settle();
env.pageUrl = "https://news.example.com/";
webviewNode().fire("did-navigate");
await settle();
check("编辑中不被外部跳转抢占", addressOf().props.value === "半截输入",
  `实际 "${addressOf().props.value}"`);

// 6) Escape 放弃编辑 → 回到真实地址
addressOf().props.onKeyDown({ key: "Escape", preventDefault() {}, target: { blur() {} } });
await settle();
check("Escape 回到真实地址", addressOf().props.value === "https://news.example.com/",
  `实际 "${addressOf().props.value}"`);

miniReact.unmount();

console.log(`\n${pass}/${pass + failures.length} 通过`);
if (failures.length) {
  console.log("失败项：\n- " + failures.join("\n- "));
  process.exit(1);
}
