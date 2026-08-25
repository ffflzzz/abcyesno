/**
 * test-agui-env-context.mjs — agui-server 回归
 *
 * 覆盖两类修复：
 *
 * (1) prependEnvContext —— 时间注入（根因：主程序 handleAgentRun 只转发裸
 *     user text，Hermes 静态 system prompt 无时间 → 模型编造日期）。
 *
 * (2) computeAppendedDelta —— 流式 delta 去重的"吃字"修复（根因：appendDelta
 *     第二道防线误用 `includes`（任意子串），导致流式输出日期/数字时，单字符
 *     数字 "2"/"0"/"6" 因已在前文出现过而被误判为"已发出"并丢弃，
 *     把 "2026年08月26日" 吃成 "206年8月日"；随后 finalize() 因头部不对齐又
 *     重发完整文本，造成"残缺+完整"两段并存。修复：改 endsWith（后缀）判断）。
 */
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  prependEnvContext,
  formatNowForModel,
  computeAppendedDelta,
  normalizeForDedup,
} = require('../electron/backend/agui-server.js');

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

// ── (1) prependEnvContext 时间注入 ───────────────────────────────────────
{
  const ctx = { forwardedProps: {} };
  const out = prependEnvContext(ctx, '今天的日期');
  check('default on: contains marker', out.startsWith('[环境上下文]'), `out=${JSON.stringify(out.slice(0, 60))}`);
  check('default on: contains time', /当前时间：/.test(out));
  check('default on: contains date rule', /不知道就说不知道/.test(out));
  check('default on: contains anti self-correction rule', /自我反思重写/.test(out));
  check('default on: keeps original prompt after separator', out.includes('---\n\n今天的日期'));
}
{
  const ctx = { forwardedProps: { env_aware: false } };
  const out = prependEnvContext(ctx, '纯用户问题');
  check('opt-out: unchanged', out === '纯用户问题', `out=${JSON.stringify(out)}`);
}
{
  const ctx = { forwardedProps: {} };
  const first = prependEnvContext(ctx, 'retry');
  const second = prependEnvContext(ctx, first);
  check('idempotent: only one prefix marker', (second.match(/\[环境上下文\]/g) || []).length === 1);
}
{
  const ctx = { forwardedProps: {} };
  check('empty string → empty', prependEnvContext(ctx, '') === '');
  check('undefined → undefined', prependEnvContext(ctx, undefined) === undefined);
  check('null → null', prependEnvContext(ctx, null) === null);
}
{
  const out = prependEnvContext({}, 'X');
  check('no forwardedProps: still injected', out.startsWith('[环境上下文]'));
}
{
  const s = formatNowForModel();
  check('formatNowForModel returns non-empty string', typeof s === 'string' && s.length > 0, `got=${JSON.stringify(s)}`);
}

// ── (2) computeAppendedDelta 吃字回归 ────────────────────────────────────
// 模拟 createTurnTranslator 的增量累积：逐字符喂 delta，维护 emittedText/emittedPlain。
function streamReplay(chars) {
  let emittedText = '';
  let emittedPlain = '';
  const out = [];
  for (const ch of chars) {
    const actual = computeAppendedDelta(emittedText, emittedPlain, ch);
    if (actual) {
      emittedText += actual;
      emittedPlain = normalizeForDedup(emittedText);
      out.push(actual);
    }
  }
  return { text: emittedText, deltas: out };
}

{
  // 回归核心：逐字符流式输出 "明天是 2026年08月26日，星期三。"，
  // 数字 "2"/"0"/"6" 多次出现，之前被 includes 吃掉。
  const target = '明天是 2026年08月26日，星期三。';
  const r = streamReplay([...target]);
  check('char-stream keeps full date', r.text === target, `got=${JSON.stringify(r.text)}`);
}

{
  // 精确复现 bug 输入：单独喂 "2" 多次，不应丢。
  const r = streamReplay([...'2026']);
  check('2026 not swallowed', r.text === '2026', `got=${JSON.stringify(r.text)}`);
}

{
  // 去重仍有效：delta 已经是 emitted 后缀时应被丢弃（防止重复打印）。
  let emittedText = '你好世界';
  let emittedPlain = normalizeForDedup(emittedText);
  const dup = computeAppendedDelta(emittedText, emittedPlain, '你好世界');
  check('full-suffix duplicate dropped', dup === '', `got=${JSON.stringify(dup)}`);
}

{
  // 去重仍有效：cumulative delta（带前文重复）应只返回新增部分。
  let emittedText = '明天是 2026年08月';
  let emittedPlain = normalizeForDedup(emittedText);
  const actual = computeAppendedDelta(emittedText, emittedPlain, '明天是 2026年08月26日');
  check('cumulative delta returns only tail', actual === '26日', `got=${JSON.stringify(actual)}`);
}

{
  // 空 delta 安全。
  check('empty delta → empty', computeAppendedDelta('abc', 'abc', '') === '');
  check('non-string delta → empty', computeAppendedDelta('abc', 'abc', null) === '');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
