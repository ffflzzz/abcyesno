/**
 * test-agui-env-context.mjs — agui-server 回归
 *
 * 覆盖两类逻辑：
 *
 * (1) prependEnvContext —— 时间注入（根因：主程序 handleAgentRun 只转发裸
 *     user text，Hermes 静态 system prompt 无时间 → 模型编造日期）。
 *
 * (2) 流式 delta 根本解 —— 已验证 Agnes 流式 content 是纯增量（数字逐字符
 *     "2026"→"2","0","2","6"；"11"→"1","1"，见 scripts/agnes_delta_probe.py）。
 *     因此 appendStreamDelta 直接透传（零去重、零阈值），finalizeRemainder
 *     用前缀/后缀精确判断补发。这替代了之前"去重误吞单字符数字"的补丁式
 *     修复（includes→endsWith→length>=2），彻底消除吃字 + 重复打印。
 */
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  prependEnvContext,
  formatNowForModel,
  buildWechatEnvLine,
  parseAppContext,
  getAppContextLines,
  appendStreamDelta,
  finalizeRemainder,
} = require('../electron/backend/agui-server.js');

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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

// ── (1.5) buildWechatEnvLine + prependEnvContext 微信行注入 ──────────────
{
  check('wechat: no status → unbound hint', (() => {
    const line = buildWechatEnvLine({});
    return line.includes('未绑定') && line.includes('微信绑定');
  })());
  check('wechat: null status → unbound hint', (() => {
    const line = buildWechatEnvLine({ getWechatStatus: () => null });
    return line.includes('未绑定');
  })());
  check('wechat: bound but not connected → state surfaced', (() => {
    const line = buildWechatEnvLine({
      getWechatStatus: () => ({ bound: true, state: 'connecting', accountMasked: 'dfb2****a1b2' }),
    });
    return line.includes('connecting') && line.includes('dfb2****a1b2');
  })());
  check('wechat: connected → notify endpoint with port', (() => {
    const line = buildWechatEnvLine({
      aguiPort: 9123,
      getWechatStatus: () => ({ bound: true, state: 'connected', accountMasked: 'dfb2****a1b2' }),
    });
    return line.includes('/api/wechat/notify') && line.includes('9123') && line.includes('dfb2****a1b2');
  })());
  check('wechat: connected via AGUI_PORT env fallback', (() => {
    const prev = process.env.AGUI_PORT;
    process.env.AGUI_PORT = '9455';
    try {
      const line = buildWechatEnvLine({
        getWechatStatus: () => ({ bound: true, state: 'connected', accountMasked: 'x' }),
      });
      return line.includes('9455');
    } finally {
      if (prev === undefined) delete process.env.AGUI_PORT; else process.env.AGUI_PORT = prev;
    }
  })());
  check('wechat line lands inside env block', (() => {
    const line = buildWechatEnvLine({
      aguiPort: 9121,
      getWechatStatus: () => ({ bound: true, state: 'connected', accountMasked: 'dfb2****a1b2' }),
    });
    const out = prependEnvContext({ forwardedProps: {} }, '完成后微信通知我', { wechatLine: line });
    return out.includes('微信桥已连接') && out.includes('---\n\n完成后微信通知我');
  })());
  check('no extras → env block unchanged (no wechat line)', (() => {
    const out = prependEnvContext({ forwardedProps: {} }, '普通问题');
    return !out.includes('微信通知');
  })());
  check('getWechatStatus throwing → safe unbound fallback', (() => {
    const line = buildWechatEnvLine({ getWechatStatus: () => { throw new Error('boom'); } });
    return line.includes('未绑定');
  })());
}

// ── (1.6) 应用自我认知块：parseAppContext + getAppContextLines ───────────
{
  const here = path.dirname(fileURLToPath(import.meta.url));
  const defaultPath = path.join(here, '..', 'electron', 'backend', 'app-context.default.json');

  check('parseAppContext: valid json → lines', (() => {
    const lines = parseAppContext(JSON.stringify({ appLines: ['a', 'b', ''] }));
    return Array.isArray(lines) && lines.length === 2 && lines[0] === 'a';
  })());
  check('parseAppContext: invalid json → null', parseAppContext('{oops') === null);
  check('parseAppContext: wrong shape → null', parseAppContext('{"nope":1}') === null);
  check('parseAppContext: all-empty lines → null', parseAppContext('{"appLines":["  ",""]}') === null);

  check('default file exists and parses with Abcyesno identity', (() => {
    const lines = getAppContextLines({ candidates: [defaultPath] });
    return lines.length >= 3 && lines.some((l) => l.includes('Abcyesno')) && lines.some((l) => l.includes('render_ui'));
  })());

  check('invalid override falls through to bundled default', (() => {
    const bad = path.join(here, 'fixtures-app-context-bad.json');
    fs.writeFileSync(bad, '{broken json', 'utf8');
    try {
      const lines = getAppContextLines({ candidates: [bad, defaultPath] });
      return lines.length >= 3 && lines.some((l) => l.includes('Abcyesno'));
    } finally {
      try { fs.unlinkSync(bad); } catch { /* ignore */ }
    }
  })());

  check('missing candidates → empty array (no crash)', (() => {
    const lines = getAppContextLines({ candidates: ['Z:/definitely/missing/app-context.json'] });
    return Array.isArray(lines) && lines.length === 0;
  })());

  check('app lines land before separator, wechat line after app lines', (() => {
    const appLines = getAppContextLines({ candidates: [defaultPath] });
    const wx = buildWechatEnvLine({
      aguiPort: 9121,
      getWechatStatus: () => ({ bound: true, state: 'connected', accountMasked: 'dfb2****a1b2' }),
    });
    const out = prependEnvContext({ forwardedProps: {} }, '正文', { appLines, wechatLine: wx });
    const appIdx = out.indexOf('Abcyesno——');
    const wxIdx = out.indexOf('微信桥已连接');
    const sepIdx = out.indexOf('---\n\n正文');
    return appIdx > -1 && wxIdx > appIdx && sepIdx > wxIdx;
  })());
}

// ── (2) appendStreamDelta：纯增量透传，零去重 ─────────────────────────────
{
  check('appendStreamDelta passes single char', appendStreamDelta('2') === '2');
  check('appendStreamDelta passes duplicate char', appendStreamDelta('1') === '1');
  check('appendStreamDelta passes word', appendStreamDelta('Windows') === 'Windows');
  check('appendStreamDelta empty → empty', appendStreamDelta('') === '');
  check('appendStreamDelta null → empty', appendStreamDelta(null) === '');
  check('appendStreamDelta non-string → empty', appendStreamDelta(123) === '');
}

// ── (3) 端到端：逐字符流式 + finalize，验证不吞字、不重复 ────────────────
// 模拟 createTurnTranslator 的完整流程：逐 delta appendStreamDelta 累积，
// 最后 message.complete 的完整文本走 finalizeRemainder 补发剩余。
function streamThenFinalize(target) {
  let emittedText = '';
  for (const ch of [...target]) {
    const actual = appendStreamDelta(ch);
    if (actual) emittedText += actual;
  }
  // message.complete 带完整文本
  const remainder = finalizeRemainder(emittedText, target);
  if (remainder) emittedText += remainder;
  return emittedText;
}

{
  // 日期（数字逐字符，之前被 includes/endsWith 吃字）
  const target = '明天是 2026年08月26日，星期三。';
  check('date streamed intact (no swallow, no dup)', streamThenFinalize(target) === target, `got=${JSON.stringify(streamThenFinalize(target))}`);
}
{
  // 连续重复字符 "11"（之前被 endsWith 单字符匹配吞第二个）
  const target = 'Windows 11 系统';
  check('duplicate chars "11" intact', streamThenFinalize(target) === target, `got=${JSON.stringify(streamThenFinalize(target))}`);
}
{
  const t00 = streamThenFinalize('00');
  check('"00" intact', t00 === '00', `got=${JSON.stringify(t00)}`);
  const t22 = streamThenFinalize('22');
  check('"22" intact', t22 === '22', `got=${JSON.stringify(t22)}`);
}
{
  // 纯数字串
  const target = '2026年08月26日';
  check('pure digits intact', streamThenFinalize(target) === target, `got=${JSON.stringify(streamThenFinalize(target))}`);
}

// ── (4) finalizeRemainder：前缀/后缀精确判断 ─────────────────────────────
{
  // 已完整流式发出（emittedText == text）→ 不补发
  check('fully streamed → empty', finalizeRemainder('你好世界', '你好世界') === '');
  // text 是 emittedText 结尾（emittedText 末尾正是 text）→ 不补发
  check('text is tail of emitted → empty', finalizeRemainder('啊你好世界', '你好世界') === '');
}
{
  // 流式发了前缀（emittedText 是 text 前缀）→ 补发剩余
  check('partial stream → remainder', finalizeRemainder('明天是 2026年08月', '明天是 2026年08月26日') === '26日');
  check('partial stream 2 → remainder', finalizeRemainder('你好', '你好世界') === '世界');
}
{
  // 完全没流式（emittedText 空）→ 补发完整
  check('nothing streamed → full text', finalizeRemainder('', '完整文本') === '完整文本');
}
{
  // drift（emittedText 和 text 头部不一致）→ 补发完整 text
  check('drift → full text', finalizeRemainder('错的头部', '正确全文') === '正确全文');
}
{
  // 空 text 安全
  check('empty text → empty', finalizeRemainder('abc', '') === '');
  check('non-string text → empty', finalizeRemainder('abc', null) === '');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
