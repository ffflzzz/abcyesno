// 本机出片服务（ComfyUI 等）—— 设置面板到短剧后台的转发层。
//
// 为什么探测与接入不在这个文件里实现：厂商档、接入档案、轮询窗口、断点续跑的
// 产地判据全在 Python 那一侧（`shortdrama/v5/vendors.py` + `v5/local_services.py`）。
// Electron 只是**又一个调用方**（与短剧前端 SPA 并列）。在这里再实现一遍探测，
// 就是同一件事两份真相 —— 迟早只对一边生效（本仓库为此栽过多次）。
//
// 因此本模块只做三件事：
//   1. 把面板的动作名映射到短剧后台的路由；
//   2. `workflow_path` 在**主进程**里读成 JSON 对象再发出去
//      （用户选的那张 .json 可能几百 KB，走 renderer 传两次没有意义，
//        而且 renderer 也读不到任意绝对路径 —— `read-file` 是工作区相对路径）；
//   3. 把信封 `{code:'000000', data}` 拆成 `{ok, data}` / `{ok:false, error}`，
//      让失败**带着后端那句人话**回到面板上（后端每条 reason 都是写给人看的）。
const fs = require('fs');

const BASE = '/v1/pixa/short-drama';

const ACTIONS = {
  status: { method: 'GET', path: '/local-services' },
  probe: { method: 'POST', path: '/local-services/probe' },
  inspect: { method: 'POST', path: '/local-services/inspect', workflow: true },
  connect: { method: 'POST', path: '/local-services/connect', workflow: true },
  default: { method: 'POST', path: '/local-services/default' },
  forget: { method: 'POST', path: '/local-services/forget' },
};

const OK_CODE = '000000';

function readWorkflow(params) {
  const p = { ...(params || {}) };
  const wp = String(p.workflow_path || '').trim();
  delete p.workflow_path;
  if (!wp) return p;
  let text = '';
  try {
    text = fs.readFileSync(wp, 'utf-8');
  } catch (err) {
    throw new Error(`读不到那张工作流：${wp}（${err.message}）`);
  }
  try {
    p.workflow = JSON.parse(text);
  } catch (err) {
    throw new Error(
      `那份 JSON 解析不了：${err.message} ⇒ 要的是 ComfyUI 菜单里 ` +
      `Workflow → Export (API) 导出的文件`);
  }
  return p;
}

// 一次请求。`baseUrl` 由调用方给（短剧后台是**懒启动**的，主进程才知道它的端口）。
async function request({ baseUrl, action, params }) {
  const spec = ACTIONS[action];
  if (!spec) return { ok: false, error: `未知的本地服务动作：${action}` };
  if (!baseUrl) {
    return { ok: false, error: '短剧后台还没起来，无法探测本机模型' };
  }
  let body = null;
  if (spec.method === 'GET') {
    body = null;
  } else {
    try {
      body = spec.workflow ? readWorkflow(params) : (params || {});
    } catch (err) {
      return { ok: false, error: String(err.message || err) };
    }
  }
  const init = { method: spec.method, headers: { 'Content-Type': 'application/json' } };
  if (body !== null) init.body = JSON.stringify(body);
  let res;
  try {
    res = await fetch(baseUrl + BASE + spec.path, init);
  } catch (err) {
    // 打不到后台 ≠ 没探到模型。这句话必须分开，否则用户会去重装 ComfyUI。
    return { ok: false, error: `连不上短剧后台（${baseUrl}）：${err.message}` };
  }
  let json = null;
  try {
    json = await res.json();
  } catch (_) {
    return { ok: false, error: `短剧后台回了非 JSON（HTTP ${res.status}）` };
  }
  if (!json || json.code !== OK_CODE) {
    const msg = (json && json.message) || `HTTP ${res.status}`;
    return { ok: false, error: msg };
  }
  return { ok: true, data: json.data };
}

module.exports = { request, ACTIONS };
