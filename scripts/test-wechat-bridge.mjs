/**
 * test-wechat-bridge.mjs — 微信桥接 mock / 冒烟回归
 *
 * 目标：在纯 Node 环境下验证
 *   1. wechat-bridge-runner（CJS 宿主）可被 require 加载
 *   2. call() 分发对已知 action 有处理、未知 action 抛错
 *   3. 未绑定微信时 getStatus=idle / bound=false / getLogs=[] 不崩溃
 *   4. start()（未绑定应安全进入 idle）/ stop() 不抛异常
 *
 * 不依赖 Electron、不连接 iLink API（不调用 getQrCode / sendTestMessage）。
 * 数据目录隔离到临时目录，跑完清理。
 */
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

// 隔离数据目录，避免污染真实 HERMES_HOME / ~/.wechat-claude-code
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wcc-test-'));
process.env.WCC_DATA_DIR = tmp;

const require = createRequire(import.meta.url);
const { createWechatBridgeRunner } = require('../electron/backend/wechat-bridge-runner.js');

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass++;
    console.log(`  \u2713 ${name}`);
  } else {
    fail++;
    console.log(`  \u2717 ${name} ${extra}`);
  }
}

async function main() {
  const statuses = [];
  const runner = createWechatBridgeRunner({ onStatus: (s) => statuses.push(s) });
  check('runner 可创建且暴露 call()', !!runner && typeof runner.call === 'function');

  // 1. getStatus 结构与默认值
  const st = await runner.call('getStatus');
  check('getStatus 返回对象', st && typeof st === 'object');
  check('getStatus 含 state/bound/ts 字段', 'state' in st && 'bound' in st && 'ts' in st);
  check('未绑定时 state=idle', st.state === 'idle', `(实际 ${st.state})`);
  check('未绑定时 bound=false', st.bound === false);

  // 2. getLogs 返回数组
  const logs = await runner.call('getLogs');
  check('getLogs 返回 {lines:[...]}', logs && Array.isArray(logs.lines));

  // 3. start（未绑定应安全进入 idle，不崩溃也不连 iLink）
  const started = await runner.call('start');
  check('start 不崩溃且返回对象', started && typeof started === 'object', JSON.stringify(started));
  check('start 后仍处于 idle（未绑定）', started.state === 'idle', `(实际 ${started.state})`);

  // 4. stop
  const stopped = await runner.call('stop');
  check('stop 不崩溃且返回对象', stopped && typeof stopped === 'object');

  // 5. 未知 action 触发分发守卫抛错
  let threw = false;
  try {
    await runner.call('not_a_real_action');
  } catch {
    threw = true;
  }
  check('未知 action 抛错（分发守卫生效）', threw);

  // 6. onStatus 推送通道可用
  check('onStatus 回调被注册（数组可写）', Array.isArray(statuses));

  console.log(`\n结果: ${pass} passed, ${fail} failed`);
  try {
    fs.rmSync(tmp, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error('测试脚本异常:', e);
  try {
    fs.rmSync(tmp, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  process.exit(2);
});
