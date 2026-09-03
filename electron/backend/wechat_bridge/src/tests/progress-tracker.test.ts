import { test } from 'node:test';
import assert from 'node:assert/strict';
import { humanizeEvent, ProgressTracker, resetProgressTracker, wechatThreadId } from '../claude/progress-tracker.js';

// ---------------------------------------------------------------------------
// humanizeEvent
// ---------------------------------------------------------------------------

test('humanizeEvent: workflow.progress running 报「N/M 步骤名」', () => {
  const h = humanizeEvent({
    name: 'workflow.progress',
    value: { step_id: 'gen_shots', stage: '分镜脚本', status: 'running', completed: 3, total: 7 },
  });
  assert.ok(h);
  assert.equal(h.text, '⏳ 3/7 分镜脚本');
  assert.equal(h.urgent, false);
});

test('humanizeEvent: workflow.progress done 报完成', () => {
  const h = humanizeEvent({
    name: 'workflow.progress',
    value: { step_id: 'gen_shots', stage: '分镜脚本', status: 'done', completed: 3, total: 7 },
  });
  assert.equal(h?.text, '✅ 3/7 分镜脚本 完成');
});

test('humanizeEvent: 缺 completed/total 时不拼假计数', () => {
  const h = humanizeEvent({
    name: 'workflow.progress',
    value: { step_id: 'x', stage: '剧本', status: 'running' },
  });
  assert.equal(h?.text, '⏳ 剧本');
});

test('humanizeEvent: workflow.artifact 带文件名且紧急', () => {
  const h = humanizeEvent({
    name: 'workflow.artifact',
    value: { id: 'final_video', type: 'video', label: '成片', path: 'C:\\out\\final.mp4' },
  });
  assert.ok(h);
  assert.equal(h.kind, 'artifact');
  assert.equal(h.urgent, true);
  assert.match(h.text, /final\.mp4$/);
});

test('humanizeEvent: workflow.approval 提示用户怎么回复', () => {
  const h = humanizeEvent({
    name: 'workflow.approval',
    value: { workflowRunId: 'wf-1', gate_id: 'g1', label: '首帧确认' },
  });
  assert.ok(h);
  assert.equal(h.urgent, true);
  assert.match(h.text, /首帧确认/);
  assert.match(h.text, /批准/);
});

test('humanizeEvent: workflow.error 带原因', () => {
  const h = humanizeEvent({ name: 'workflow.error', value: { message: '视频合成超时' } });
  assert.ok(h);
  assert.match(h.text, /视频合成超时/);
});

test('humanizeEvent: workflow.done 区分 done / rejected / timeout / failed', () => {
  assert.match(humanizeEvent({ name: 'workflow.done', value: { status: 'done' } })?.text ?? '', /完成/);
  assert.match(humanizeEvent({ name: 'workflow.done', value: { status: 'rejected' } })?.text ?? '', /中止/);
  assert.match(humanizeEvent({ name: 'workflow.done', value: { status: 'timeout' } })?.text ?? '', /超时/);
  assert.match(humanizeEvent({ name: 'workflow.done', value: { status: 'error', error: 'boom' } })?.text ?? '', /失败/);
});

test('humanizeEvent: workflow.graph / trace / 噪音事件不产生文案', () => {
  assert.equal(humanizeEvent({ name: 'workflow.graph', value: { nodes: [] } }), null);
  assert.equal(humanizeEvent({ name: 'tool.chunk', value: { chunk: 'raw noise' } }), null);
  assert.equal(humanizeEvent({ name: 'thinking.delta', value: { text: '让我想想' } }), null);
  // trace 只更新当前节点，不进聊天流
  const trace = humanizeEvent({ name: 'workflow.trace', value: { node: 'gen_shots', status: 'running' } });
  assert.ok(trace);
  assert.equal(trace.text, '');
  assert.equal(trace.trackNode, 'gen_shots');
});

test('humanizeEvent: stream.phase 是空转占位，一律不推送（2026-09-03 用户反馈）', () => {
  assert.equal(humanizeEvent({ name: 'stream.phase', value: { phase: 'tool_executing' } }), null);
  assert.equal(humanizeEvent({ name: 'stream.phase', value: { phase: 'thinking' } }), null);
  assert.equal(humanizeEvent({ name: 'stream.phase', value: { phase: 'text_generating' } }), null);
  assert.equal(humanizeEvent({ name: 'stream.phase', value: { phase: 'idle' } }), null);
});

// ---------------------------------------------------------------------------
// tool.call / tool.error —— 普通聊天长任务的真实工作过程
// ---------------------------------------------------------------------------

test('humanizeEvent: tool.call 取 query 键做参数摘要', () => {
  const h = humanizeEvent({
    name: 'tool.call',
    value: { toolName: 'web_search', argsText: '{"query":"Google 发布 Gemini 3.8 Flash","limit":5}' },
  });
  assert.ok(h);
  assert.equal(h.text, '🔧 web_search：Google 发布 Gemini 3.8 Flash');
  assert.equal(h.urgent, false);
  assert.equal(h.trackStep, 'web_search（Google 发布 Gemini 3.8 Flash）');
});

test('humanizeEvent: tool.call 无命中键时取第一个像人话的字符串', () => {
  const h = humanizeEvent({
    name: 'tool.call',
    value: { toolName: 'translate', argsText: '{"lang":"en","body":"把这段话翻译成英文"}' },
  });
  assert.ok(h);
  assert.equal(h.text, '🔧 translate：en');
});

test('humanizeEvent: tool.call 跳过 base64/dataURL，退回下一个可用值', () => {
  const bigB64 = `data:image/png;base64,${'A'.repeat(300)}`;
  const h = humanizeEvent({
    name: 'tool.call',
    value: { toolName: 'vision', argsText: JSON.stringify({ image: bigB64, note: '识别这张截图' }) },
  });
  assert.ok(h);
  assert.equal(h.text, '🔧 vision：识别这张截图');
});

test('humanizeEvent: tool.call 无参数时只报工具名', () => {
  const h = humanizeEvent({ name: 'tool.call', value: { toolName: 'get_time', argsText: '' } });
  assert.ok(h);
  assert.equal(h.text, '🔧 get_time');
  assert.equal(h.trackStep, 'get_time');
});

test('humanizeEvent: tool.call args 不是合法 JSON 时截原始文本', () => {
  const h = humanizeEvent({ name: 'tool.call', value: { toolName: 't', argsText: 'raw text args' } });
  assert.ok(h);
  assert.equal(h.text, '🔧 t：raw text args');
});

test('humanizeEvent: tool.error 紧急报失败', () => {
  const h = humanizeEvent({ name: 'tool.error', value: { toolName: 'web_fetch' } });
  assert.ok(h);
  assert.equal(h.text, '⚠️ web_fetch 执行失败');
  assert.equal(h.urgent, true);
});

test('humanizeEvent: 空/畸形输入安全返回 null 且不抛', () => {
  assert.equal(humanizeEvent(null), null);
  assert.equal(humanizeEvent(undefined), null);
  assert.equal(humanizeEvent({}), null);
  assert.equal(humanizeEvent({ name: '' }), null);
  // value 缺失时退化成通用文案而不是抛错
  assert.equal(humanizeEvent({ name: 'workflow.progress' })?.text, '⏳ 处理中');
});

// ---------------------------------------------------------------------------
// ProgressTracker
// ---------------------------------------------------------------------------

function progress(step: string, label: string, completed: number, total: number, status = 'running') {
  return { name: 'workflow.progress', value: { step_id: step, stage: label, status, completed, total } };
}

test('tracker: 同一节点同一状态在 TTL 内只推一次（去重）', () => {
  const t = new ProgressTracker('wx-t1');
  assert.ok(t.ingest(progress('gen_shots', '分镜脚本', 3, 7)));
  assert.equal(t.ingest(progress('gen_shots', '分镜脚本', 3, 7)), null, '重复事件应被去重');
});

test('tracker: 被去重/节流挡下的事件仍然记账，/进度 看得到', () => {
  const t = new ProgressTracker('wx-t2');
  t.ingest(progress('a', '剧本', 1, 5));
  t.ingest(progress('a', '剧本', 1, 5)); // 被去重
  assert.equal(t.allEntries().length, 2);
  const report = t.renderReport();
  assert.match(report, /剧本/);
});

test('tracker: 非紧急进度受节流约束，紧急事件绕过节流', () => {
  const t = new ProgressTracker('wx-t3');
  assert.ok(t.ingest(progress('a', '剧本', 1, 5)), '第一条应放行');
  assert.equal(t.ingest(progress('b', '分镜', 2, 5)), null, '20s 内第二条应被节流');
  // 紧急事件不受节流影响
  const urgent = t.ingest({ name: 'workflow.error', value: { message: '炸了' } });
  assert.ok(urgent);
  assert.match(urgent.text, /炸了/);
});

test('tracker: trace 事件只更新 currentStep 兜底，不推送也不记账', () => {
  const t = new ProgressTracker('wx-t4');
  assert.equal(t.ingest({ name: 'workflow.trace', value: { node: 'gen_shots', status: 'running' } }), null);
  assert.equal(t.allEntries().length, 0);
  assert.equal(t.currentStepText(), 'gen_shots', 'trace 节点应作为 currentStep 兜底');
  // 一旦有 progress，用更可读的 label 覆盖
  t.ingest(progress('gen_shots', '分镜脚本', 3, 7));
  assert.equal(t.currentStepText(), '3/7 分镜脚本');
});

test('tracker: 同一毫秒内涌入的普通进度只放行一条（节流）', () => {
  const t = new ProgressTracker('wx-t5');
  for (let i = 0; i < 30; i++) {
    t.ingest(progress(`step-${i}`, `步骤${i}`, i, 30));
  }
  const emitted = t.allEntries().filter((e) => e.emitted);
  assert.equal(emitted.length, 1, `爆发式进度应只放行 1 条，实际 ${emitted.length}`);
  // 但 30 条全部记账，/进度 与补发摘要看得到全貌
  assert.equal(t.allEntries().length, 30);
});

test('tracker: 紧急事件（错误/审批/产物/完成）绕过节流逐条放行', () => {
  const t = new ProgressTracker('wx-t5b');
  for (let i = 0; i < 5; i++) {
    assert.ok(t.ingest({ name: 'workflow.error', value: { message: `第${i}次失败`, node: `n${i}` } }));
  }
  assert.equal(t.allEntries().filter((e) => e.emitted).length, 5);
});

test('tracker: 推送失败才产生补发摘要；全部送达则不打扰', () => {
  const t = new ProgressTracker('wx-t6');
  const line = t.ingest(progress('a', '剧本', 1, 3));
  assert.ok(line);
  t.markDelivered(line.entry);
  assert.equal(t.hasDigest(), false, '全部送达时不应产生摘要');

  // 第二条用紧急事件（绕过节流），模拟它在回复窗口关闭时推送失败
  const line2 = t.ingest({ name: 'workflow.artifact', value: { id: 'shots', label: '分镜表', path: '/out/shots.json' } });
  assert.ok(line2);
  t.noteSendFailure(line2.entry);
  assert.equal(t.hasDigest(), true);

  const digest = t.takeDigest();
  assert.ok(digest);
  assert.match(digest, /剧本/);
  assert.match(digest, /分镜/);
  // 摘要只给一次
  assert.equal(t.hasDigest(), false);
  assert.equal(t.takeDigest(), null);
});

test('tracker: 摘要按 key 去重并限制条数', () => {
  const t = new ProgressTracker('wx-t7');
  for (let i = 0; i < 20; i++) {
    const l = t.ingest(progress(`s${i}`, `步骤${i}`, i, 20));
    if (l) t.noteSendFailure(l.entry);
  }
  t.noteSendFailure();
  const digest = t.takeDigest();
  assert.ok(digest);
  const bullets = digest.split('\n').filter((l) => l.startsWith('· '));
  assert.ok(bullets.length <= 8, `摘要条数应 <= 8，实际 ${bullets.length}`);
});

test('tracker: reset 清空上一轮状态', () => {
  const t = new ProgressTracker('wx-t8');
  t.ingest(progress('a', '剧本', 1, 3));
  t.reset();
  assert.equal(t.allEntries().length, 0);
  assert.equal(t.currentStepText(), '');
  assert.equal(t.hasDigest(), false);
});

test('tracker: renderReport 在没有任务时给出明确答复', () => {
  const t = new ProgressTracker('wx-t9');
  assert.match(t.renderReport(), /没有进行中的任务/);
});

test('tracker: renderReport 展示当前步骤与已运行时长', () => {
  const t = new ProgressTracker('wx-t10');
  t.ingest(progress('a', '分镜脚本', 3, 7));
  const report = t.renderReport();
  assert.match(report, /正在做：3\/7 分镜脚本/);
  assert.match(report, /已运行/);
});

test('tracker: tool.call 被节流挡下时 currentStep 仍然更新（keepalive 有真实内容可报）', () => {
  const t = new ProgressTracker('wx-t11');
  assert.ok(t.ingest({ name: 'tool.call', value: { toolName: 'web_search', argsText: '{"query":"第一次搜索"}' } }),
    '第一条应放行');
  assert.equal(t.currentStepText(), 'web_search（第一次搜索）');
  // 20s 节流窗口内的第二条：不推送，但步骤必须跟上
  assert.equal(t.ingest({ name: 'tool.call', value: { toolName: 'web_fetch', argsText: '{"url":"https://example.com/a"}' } }), null);
  assert.equal(t.currentStepText(), 'web_fetch（https://example.com/a）');
  // 记账仍在：/进度 与补发摘要看得到
  assert.equal(t.allEntries().length, 2);
});

test('tracker: 相同工具+相同参数在 TTL 内去重', () => {
  const t = new ProgressTracker('wx-t12');
  const ev = { name: 'tool.call', value: { toolName: 'web_search', argsText: '{"query":"同一次搜索"}' } };
  assert.ok(t.ingest(ev));
  assert.equal(t.ingest(ev), null, '重复调用应被去重');
  assert.equal(t.allEntries().length, 2, '去重的仍然记账');
});

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

test('registry: resetProgressTracker 换掉旧实例', () => {
  const id = wechatThreadId('user-abc');
  assert.equal(id, 'wx-user-abc');
  const first = resetProgressTracker(id);
  first.ingest(progress('a', '剧本', 1, 3));
  const second = resetProgressTracker(id);
  assert.notEqual(first, second);
  assert.equal(second.allEntries().length, 0);
});
