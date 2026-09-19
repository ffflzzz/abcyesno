// 最小 JSX 运行时：esbuild --jsx=automatic 只要求 jsx/jsxs/jsxDEV/Fragment，
// 产出 { type, props } 的普通对象即可被用例遍历断言。
import { webviewNode } from "./env.mjs";

export const Fragment = Symbol.for("bp.Fragment");

function make(type, props) {
  const p = props || {};
  // 真实 React 在提交阶段以 DOM 节点调用 ref；这里在构造节点时立即调用，
  // 让组件里"ref 回调负责绑定 webview 事件"的写法照常生效。
  if (type === "webview" && typeof p.ref === "function") {
    p.ref(webviewNode());
  }
  return { type, props: p };
}

export const jsx = make;
export const jsxs = make;
export const jsxDEV = make;
export default { jsx, jsxs, jsxDEV, Fragment };
