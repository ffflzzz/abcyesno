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
// TOOL_CALL_*：真实工作过程（2026-09-03 修复）
// ---------------------------------------------------------------------------
// agui-server 会把 Hermes 的 tool.start/complete 翻译成标准 AG-UI 的
// TOOL_CALL_START + TOOL_CALL_ARGS（一次性全量 JSON）+ TOOL_CALL_END。
// 之前桥这里 default: break 全丢 —— 用户看到的全是「思考中/执行工具」占位，
// 真正搜了什么、跑了什么一条都看不到。

test('TOOL_CALL_START + ARGS 合成一条 tool.call（含工具名与参数原文）', () => {
  const state = freshState();
  const seen: any[] = [];
  const cb = { onCustom: (ev: any) => seen.push(ev) };
  handleAgUiEvent({ type: 'TOOL_CALL_START', toolCallId: 'tc-1', toolCallName: 'web_search' }, state, cb);
  handleAgUiEvent(
    { type: 'TOOL_CALL_ARGS', toolCallId: 'tc-1', delta: '{"query":"Gemini 3.8 Flash","limit":5}' },
    state,
    cb,
  );
  assert.equal(seen.length, 1, 'START 不发消息，ARGS 才合成 tool.call');
  assert.equal(seen[0].name, 'tool.call');
  assert.equal(seen[0].value.toolName, 'web_search');
  assert.equal(seen[0].value.argsText, '{"query":"Gemini 3.8 Flash","limit":5}');
  assert.equal(seen[0].value.toolCallId, 'tc-1');
});

test('TOOL_CALL_ARGS 分片到达时累积成完整 argsText', () => {
  const state = freshState();
  const seen: any[] = [];
  const cb = { onCustom: (ev: any) => seen.push(ev) };
  handleAgUiEvent({ type: 'TOOL_CALL_START', toolCallId: 'tc-2', toolCallName: 'write' }, state, cb);
  handleAgUiEvent({ type: 'TOOL_CALL_ARGS', toolCallId: 'tc-2', delta: '{"content":"第一段' }, state, cb);
  handleAgUiEvent({ type: 'TOOL_CALL_ARGS', toolCallId: 'tc-2', delta: '第二段"}' }, state, cb);
  assert.equal(seen.length, 2);
  assert.equal(seen[1].value.argsText, '{"content":"第一段第二段"}');
});

test('TOOL_CALL_END failed=true 合成 tool.error；成功则不产生任何消息', () => {
  const state = freshState();
  const seen: any[] = [];
  const cb = { onCustom: (ev: any) => seen.push(ev) };
  handleAgUiEvent({ type: 'TOOL_CALL_START', toolCallId: 'tc-3', toolCallName: 'terminal' }, state, cb);
  handleAgUiEvent({ type: 'TOOL_CALL_END', toolCallId: 'tc-3', durationMs: 1200 }, state, cb);
  assert.equal(seen.length, 0, '成功完成的工具不报「过程」');

  handleAgUiEvent({ type: 'TOOL_CALL_START', toolCallId: 'tc-4', toolCallName: 'terminal' }, state, cb);
  handleAgUiEvent({ type: 'TOOL_CALL_END', toolCallId: 'tc-4', failed: true }, state, cb);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].name, 'tool.error');
  assert.equal(seen[0].value.toolName, 'terminal');
});

test('TOOL_CALL_END 之后清理内部状态（不跨工具串名）', () => {
  const state = freshState();
  const cb = { onCustom: () => {} };
  handleAgUiEvent({ type: 'TOOL_CALL_START', toolCallId: 'tc-5', toolCallName: 'a' }, state, cb);
  handleAgUiEvent({ type: 'TOOL_CALL_END', toolCallId: 'tc-5' }, state, cb);
  assert.equal(state.toolNames?.['tc-5'], undefined);
  assert.equal(state.toolArgs?.['tc-5'], undefined);
});

test('TOOL_CALL_* 缺字段时不抛错', () => {
  const state = freshState();
  assert.doesNotThrow(() => {
    handleAgUiEvent({ type: 'TOOL_CALL_START' }, state, {});
    handleAgUiEvent({ type: 'TOOL_CALL_ARGS' }, state, {});
    handleAgUiEvent({ type: 'TOOL_CALL_END' }, state, {});
  });
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
