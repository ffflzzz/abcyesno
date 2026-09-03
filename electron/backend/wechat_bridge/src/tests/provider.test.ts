import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handleAgUiEvent, SseFrameReader, type AgUiParserState } from '../claude/provider.js';

function freshState(): AgUiParserState {
  return { messageId: null, textParts: [], finished: false };
}

// ---------------------------------------------------------------------------
// 文本流
// ---------------------------------------------------------------------------

test('TEXT_MESSAGE_START 记住 messageId', () => {
  const state = freshState();
  handleAgUiEvent({ type: 'TEXT_MESSAGE_START', messageId: 'm-1' }, state, {});
  assert.equal(state.messageId, 'm-1');
});

test('TEXT_MESSAGE_CONTENT 累积文本并回调 onText', () => {
  const state = freshState();
  const calls: string[] = [];
  handleAgUiEvent({ type: 'TEXT_MESSAGE_CONTENT', delta: '你好' }, state, { onText: (t) => calls.push(t) });
  handleAgUiEvent({ type: 'TEXT_MESSAGE_CONTENT', delta: '世界' }, state, { onText: (t) => calls.push(t) });
  assert.deepEqual(calls, ['你好', '世界']);
  assert.equal(state.textParts.join(''), '你好世界');
});

test('TEXT_MESSAGE_CONTENT 跳过 "Operation interrupted:" 哨兵', () => {
  const state = freshState();
  const calls: string[] = [];
  handleAgUiEvent({ type: 'TEXT_MESSAGE_CONTENT', delta: 'Operation interrupted: by user' }, state, {
    onText: (t) => calls.push(t),
  });
  assert.deepEqual(calls, []);
  assert.deepEqual(state.textParts, []);
});

// ---------------------------------------------------------------------------
// CUSTOM：进度可观察通道（2026-09-01 修复的回归防线）
// ---------------------------------------------------------------------------
// 之前 handleAgUiEvent 对 CUSTOM 只有 `default: break`，整条 workflow.* 过程
// 通道被静默丢弃 —— 长任务期间微信侧除了保活文案什么都看不到。

test('CUSTOM 事件必须转发给 onCustom（核心回归点）', () => {
  const state = freshState();
  const seen: any[] = [];
  handleAgUiEvent(
    { type: 'CUSTOM', name: 'workflow.progress', value: { completed: 2, total: 5 } },
    state,
    { onCustom: (ev) => seen.push(ev) },
  );
  assert.equal(seen.length, 1);
  assert.equal(seen[0].name, 'workflow.progress');
  assert.deepEqual(seen[0].value, { completed: 2, total: 5 });
});

test('CUSTOM 不推进文本流、不触发 onText/onTurnEnd', () => {
  const state = freshState();
  let textCalls = 0;
  let turnEndCalls = 0;
  const done = handleAgUiEvent({ type: 'CUSTOM', name: 'stream.phase', value: { phase: 'thinking' } }, state, {
    onText: () => textCalls++,
    onTurnEnd: () => turnEndCalls++,
  });
  assert.equal(done, false);
  assert.equal(textCalls, 0);
  assert.equal(turnEndCalls, 0);
  assert.equal(state.finished, false);
});

test('onCustom 抛错不会打断主文本流（进度渲染是尽力而为）', () => {
  const state = freshState();
  assert.doesNotThrow(() => {
    handleAgUiEvent({ type: 'CUSTOM', name: 'workflow.progress', value: {} }, state, {
      onCustom: () => {
        throw new Error('boom');
      },
    });
  });
  // 后续文本事件照常处理
  const calls: string[] = [];
  handleAgUiEvent({ type: 'TEXT_MESSAGE_CONTENT', delta: '仍然工作' }, state, {
    onText: (t) => calls.push(t),
  });
  assert.deepEqual(calls, ['仍然工作']);
});

test('没有注册 onCustom 时 CUSTOM 事件安全跳过', () => {
  const state = freshState();
  assert.equal(handleAgUiEvent({ type: 'CUSTOM', name: 'workflow.done', value: {} }, state, {}), false);
});

// ---------------------------------------------------------------------------
// 终态
// ---------------------------------------------------------------------------

test('RUN_FINISHED 置 finished 并回调 end_turn', () => {
  const state = freshState();
  const calls: string[] = [];
  const done = handleAgUiEvent({ type: 'RUN_FINISHED' }, state, { onTurnEnd: (r) => calls.push(r) });
  assert.equal(done, true);
  assert.equal(state.finished, true);
  assert.deepEqual(calls, ['end_turn']);
});

test('RUN_ERROR 记录 error 并回调 error', () => {
  const state = freshState();
  const calls: string[] = [];
  const done = handleAgUiEvent({ type: 'RUN_ERROR', message: 'boom' }, state, { onTurnEnd: (r) => calls.push(r) });
  assert.equal(done, true);
  assert.equal(state.errorMessage, 'boom');
  assert.deepEqual(calls, ['error']);
});

test('未知事件类型安全忽略', () => {
  const state = freshState();
  assert.equal(handleAgUiEvent({ type: 'STEP_STARTED' }, state, {}), false);
  assert.equal(handleAgUiEvent(null, state, {}), false);
  assert.equal(handleAgUiEvent({}, state, {}), false);
});

// ---------------------------------------------------------------------------
// SSE 分帧
// ---------------------------------------------------------------------------

test('SseFrameReader 按 \\n\\n 切帧并解析 data: JSON', () => {
  const r = new SseFrameReader();
  // 第一块故意在一个事件中间截断，验证增量缓冲
  const first = r.push('data: {"type":"TEXT_MESSAGE_START","messageId":"m-1"}\n\ndata: {"type":"TEXT_MES');
  assert.equal(first.length, 1);
  assert.equal(first[0].messageId, 'm-1');

  const second = r.push('SAGE_CONTENT","delta":"好"}\n\n');
  assert.equal(second.length, 1);
  assert.equal(second[0].delta, '好');
});

test('SseFrameReader 忽略非 data: 行与坏 JSON', () => {
  const r = new SseFrameReader();
  const evs = r.push('event: ping\n\ndata: not-json\n\ndata: {"ok":1}\n\n');
  assert.equal(evs.length, 1);
  assert.equal(evs[0].ok, 1);
});
