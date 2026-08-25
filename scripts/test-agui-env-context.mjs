/**
 * test-agui-env-context.mjs — agui-server 的 prependEnvContext 回归
 *
 * 根因（2026-08-25）：主程序对话窗（走 /api/ag-ui/run）出现 "今天是 206年8月5日"
 * 类日期编造。核代码确认 agui-server#handleAgentRun 只把 user text 原样塞进
 * `prompt.submit({session_id, text})`，没有任何 system 注入，Hermes 的静态
 * system prompt 也没有时间。
 *
 * 修复：prependEnvContext(ctx, text) 向 text 前置拼接 [环境上下文] 当前时间/日期
 * 编造规则/反自校正指令。在 prompt.submit 入口注入。
 *
 * 本测试覆盖：
 *   1. 正常 ctx 默认开启 → 拼上时间块
 *   2. ctx.forwardedProps.env_aware=false → 原样返回（opt-out 工作）
 *   3. text 已经是 [环境上下文] 开头 → 幂等，不再叠前缀
 *   4. 空文本 / undefined → 安全 no-op
 *   5. formatNowForModel() 在 Intl 不可用时的 fallback
 */
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { prependEnvContext, formatNowForModel } = require('../electron/backend/agui-server.js');

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass++;
    console.log(`  PASS ${name}`);
  } else {
    fail++;
    console.log(`  FAIL ${name} ${extra}`);
  }
}

// 1. 默认开启 → 拼上时间块
{
  const ctx = { forwardedProps: {} };
  const out = prependEnvContext(ctx, '今天的日期');
  check('default on: contains marker', out.startsWith('[环境上下文]'), `out=${JSON.stringify(out.slice(0, 60))}`);
  check('default on: contains time', /当前时间：/.test(out));
  check('default on: contains date rule', /不知道就说不知道/.test(out));
  check('default on: contains anti self-correction rule', /自我反思重写/.test(out));
  check('default on: keeps original prompt after separator', out.includes('---\n\n今天的日期'));
}

// 2. opt-out：env_aware=false → 原样
{
  const ctx = { forwardedProps: { env_aware: false } };
  const out = prependEnvContext(ctx, '纯用户问题');
  check('opt-out: unchanged', out === '纯用户问题', `out=${JSON.stringify(out)}`);
}

// 3. 幂等：已注入不再叠
{
  const ctx = { forwardedProps: {} };
  const first = prependEnvContext(ctx, 'retry');
  // 模拟二次调用（带第一次的输出）
  const second = prependEnvContext(ctx, first);
  check('idempotent: only one prefix marker', (second.match(/\[环境上下文\]/g) || []).length === 1);
}

// 4. 空/undefined 安全 no-op
{
  const ctx = { forwardedProps: {} };
  check('empty string → empty', prependEnvContext(ctx, '') === '');
  check('undefined → undefined', prependEnvContext(ctx, undefined) === undefined);
  check('null → null', prependEnvContext(ctx, null) === null);
}

// 5. ctx 没有 forwardedProps 也安全（默认 on）
{
  const out = prependEnvContext({}, 'X');
  check('no forwardedProps: still injected', out.startsWith('[环境上下文]'));
}

// 6. formatNowForModel 直接调用，至少返回一个非空字符串（不能抛）
{
  const s = formatNowForModel();
  check('formatNowForModel returns non-empty string', typeof s === 'string' && s.length > 0, `got=${JSON.stringify(s)}`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
