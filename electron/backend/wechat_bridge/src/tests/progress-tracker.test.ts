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

test('humanizeEvent: stream.phase 本身永远不是一条消息（切出 thinking 只触发 flush）', () => {
  const out = humanizeEvent({ name: 'stream.phase', value: { phase: 'tool_executing' } });
  assert.ok(out);
  assert.equal(out.text, '');
  assert.equal(out.flushBuf, true, '切出 thinking 应触发思考缓冲 flush');
  assert.equal(humanizeEvent({ name: 'stream.phase', value: { phase: 'thinking' } }), null, '进入 thinking 只管继续攒');
  assert.equal(humanizeEvent({ name: 'stream.phase', value: { phase: 'idle' } })?.flushBuf, true);
});

// ---------------------------------------------------------------------------
// tool.call（silent）/ tool.error
// ---------------------------------------------------------------------------

test('humanizeEvent: tool.call 取 query 键做参数摘要，且标记为 silent（不进聊天流）', () => {
  const h = humanizeEvent({
    name: 'tool.call',
    value: { toolName: 'web_search', argsText: '{"query":"Google 发布 Gemini 3.8 Flash","limit":5}' },
  });
  assert.ok(h);
  assert.equal(h.text, '🔧 web_search：Google 发布 Gemini 3.8 Flash');
  assert.equal(h.urgent, false);
  assert.equal(h.silent, true, '工具调用行只记账不推送（2026-09-03 二次反馈）');
  assert.equal(h.trackStep, 'web_search（Google 发布 Gemini 3.8 Flash）');
});

test('humanizeEvent: tool.call 剥掉 payload 包装键，从 arguments 里取真参数', () => {
  // Hermes 偶尔把整个 tool.start payload 当 args：外面只有 call_id 这类回执
  const h = humanizeEvent({
    name: 'tool.call',
    value: {
      toolName: 'web_search',
      argsText: '{"tool_id":"call_eb2ece4c386d4da49df82523","name":"web_search","arguments":{"query":"METR 智能体攻击事件调查报告"}}',
    },
  });
  assert.ok(h);
  assert.equal(h.text, '🔧 web_search：METR 智能体攻击事件调查报告');
});

test('humanizeEvent: tool.call 兜底时跳过 call_id 等回执值', () => {
  const h = humanizeEvent({
    name: 'tool.call',
    value: { toolName: 'web_search', argsText: '{"tool_id":"call_78af5f9eae7d40b09c440ad7","note":"查一下原文"}' },
  });
  assert.ok(h);
  assert.equal(h.text, '🔧 web_search：查一下原文');
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

test('tracker: tool.call 一律 silent——不推送、但记账且 currentStep 跟上', () => {
  const t = new ProgressTracker('wx-t11');
  assert.equal(t.ingest({ name: 'tool.call', value: { toolName: 'web_search', argsText: '{"query":"第一次搜索"}' } }), null,
    '工具调用行不进聊天流');
  assert.equal(t.currentStepText(), 'web_search（第一次搜索）', 'keepalive 仍知道在跑什么');
  assert.equal(t.ingest({ name: 'tool.call', value: { toolName: 'web_fetch', argsText: '{"url":"https://example.com/a"}' } }), null);
  assert.equal(t.currentStepText(), 'web_fetch（https://example.com/a）');
  // 记账仍在：/进度 与补发摘要看得到完整工具轨迹
  assert.equal(t.allEntries().length, 2);
  assert.ok(t.allEntries().every((e) => !e.emitted), 'silent 条目永不 emitted');
});

test('tracker: tool.call silent 记账不占节流预算', () => {
  const t = new ProgressTracker('wx-t11b');
  t.ingest({ name: 'tool.call', value: { toolName: 'a', argsText: '{"q":"1"}' } });
  t.ingest({ name: 'tool.call', value: { toolName: 'b', argsText: '{"q":"2"}' } });
  // silent 不动 lastEmitAt —— 紧随其后的普通进度应立即放行
  assert.ok(t.ingest(progress('a', '剧本', 1, 5)), 'silent 不应消耗节流窗口');
});

// ---------------------------------------------------------------------------
// 思考可观察（2026-09-03 二次反馈：用户要看 agent 在想啥）
// ---------------------------------------------------------------------------

function reasoning(text: string) {
  return { name: 'reasoning.delta', value: { text } };
}

test('tracker: reasoning.delta 只进缓冲不推送，切出 thinking 时整句 flush 成 💭', () => {
  const t = new ProgressTracker('wx-t13');
  assert.equal(t.ingest(reasoning('用户想知道 METR 报告的可信度，')), null, 'delta 阶段不推送');
  assert.equal(t.ingest(reasoning('我先搜原始报告再交叉验证各家的说法。')), null);
  assert.equal(t.allEntries().length, 0, '缓冲中不记账');

  const line = t.ingest({ name: 'stream.phase', value: { phase: 'tool_executing' } });
  assert.ok(line, '阶段切出 thinking 应 flush');
  assert.match(line.text, /^💭 /);
  assert.match(line.text, /我先搜原始报告再交叉验证/);
  assert.match(t.currentStepText(), /^思考：/);
  assert.equal(t.allEntries().length, 1);
  assert.ok(t.allEntries()[0].emitted);
});

test('tracker: flush 取尾部（最新的想法最值钱），超长加省略号', () => {
  const t = new ProgressTracker('wx-t14');
  t.ingest(reasoning('开头铺垫'.repeat(40))); // 320 字
  t.ingest(reasoning('结尾才是真正的结论：报告可信度存疑'));
  const line = t.ingest({ name: 'stream.phase', value: { phase: 'text_generating' } });
  assert.ok(line);
  assert.match(line.text, /报告可信度存疑$/, '应保留思考的结尾');
  assert.ok(line.text.length <= 115, `💭 单句应 <= ~110 字，实际 ${line.text.length}`);
});

test('tracker: 缓冲攒满 600 字符自动 flush，不必等阶段切换', () => {
  const t = new ProgressTracker('wx-t15');
  let line: ReturnType<ProgressTracker['ingest']> = null;
  for (let i = 0; i < 7; i++) {
    line = t.ingest(reasoning('x'.repeat(100))); // 第 7 次 = 700 > 600
    if (line) break;
  }
  assert.ok(line, '超长思考应中途自动 flush');
  assert.match(line!.text, /^💭 /);
  // 缓冲已清空：同毫秒内的阶段事件不再产生第二条
  assert.equal(t.ingest({ name: 'stream.phase', value: { phase: 'tool_executing' } }), null, '缓冲已清空');
});

test('tracker: reasoning.snapshot 一次性整块思考直接取尾部推', () => {
  const t = new ProgressTracker('wx-t16');
  const line = t.ingest({ name: 'reasoning.snapshot', value: { text: `${'铺垫内容。'.repeat(60)}最终判断：该报告样本量偏小` } });
  assert.ok(line);
  assert.match(line.text, /^💭 /);
  assert.match(line.text, /最终判断：该报告样本量偏小$/);
});

test('tracker: 思考 flush 也受去重/节流约束，但 currentStep 无论如何都更新', () => {
  const t = new ProgressTracker('wx-t17');
  t.ingest(reasoning('第一段思考内容'));
  const first = t.ingest({ name: 'stream.phase', value: { phase: 'tool_executing' } });
  assert.ok(first, '第一次 flush 应放行');

  t.ingest(reasoning('第二段思考内容'));
  const second = t.ingest({ name: 'stream.phase', value: { phase: 'text_generating' } });
  assert.equal(second, null, '20s 内第二次 flush 应被节流');
  assert.match(t.currentStepText(), /第二段思考内容/, '步骤仍应跟上');
  assert.equal(t.allEntries().length, 2, '被节流的思考仍记账');
  assert.ok(t.allEntries()[1].emitted === false);
});

test('tracker: thinking.delta（浅层指示器）不进思考缓冲', () => {
  const t = new ProgressTracker('wx-t18');
  t.ingest({ name: 'thinking.delta', value: { text: '让我想想' } });
  const line = t.ingest({ name: 'stream.phase', value: { phase: 'tool_executing' } });
  assert.equal(line, null, '浅层指示器不应产生 💭');
  assert.equal(t.allEntries().length, 0);
});

test('tracker: tool.call 触发思考 flush（有些路径 phase 不变）', () => {
  const t = new ProgressTracker('wx-t19');
  t.ingest(reasoning('决定先去搜一下原文链接'));
  const line = t.ingest({ name: 'tool.call', value: { toolName: 'web_search', argsText: '{"query":"METR"}' } });
  assert.ok(line, '工具调用前应先把攒的思考放出去');
  assert.match(line.text, /^💭 /);
  assert.match(line.text, /决定先去搜一下原文链接/);
});

// ---------------------------------------------------------------------------
// todo.update —— 待办面板（docs/todos-panel.md §6.1）
// ---------------------------------------------------------------------------

function todoUpdate(items: Array<{ id?: string; content: string; status: string }>) {
  return { name: 'todo.update', value: { ts: Date.now(), items } };
}

test('humanizeEvent: todo.update 首次报计划与前 3 条预览', () => {
  const h = humanizeEvent(todoUpdate([
    { id: '1', content: '分析现有项目结构', status: 'pending' },
    { id: '2', content: '创建 brief.json', status: 'in_progress' },
    { id: '3', content: '生成分镜表', status: 'pending' },
    { id: '4', content: '导出剪映草稿', status: 'pending' },
  ]));
  assert.ok(h);
  assert.match(h.text, /^📋 计划 4 步：/);
  assert.match(h.text, /1\) 分析现有项目结构/);
  assert.equal(h.urgent, false);
  assert.match(h.trackStep || '', /创建 brief\.json/, '步骤应指向进行中的那一条');
});

test('humanizeEvent: todo.update 有进展时报完成数与最新完成项', () => {
  const h = humanizeEvent(todoUpdate([
    { id: '1', content: '分析现有项目结构', status: 'completed' },
    { id: '2', content: '创建 brief.json', status: 'completed' },
    { id: '3', content: '生成分镜表', status: 'in_progress' },
  ]));
  assert.ok(h);
  assert.match(h.text, /^✅ 2\/3 创建 brief\.json 已完成$/);
});

test('humanizeEvent: todo.update 全部完成时报收尾', () => {
  const h = humanizeEvent(todoUpdate([
    { id: '1', content: 'A', status: 'completed' },
    { id: '2', content: 'B', status: 'completed' },
  ]));
  assert.ok(h);
  assert.match(h.text, /^✅ 计划 2 步全部完成$/);
});

test('humanizeEvent: todo.update cancelled 不计入分母', () => {
  const h = humanizeEvent(todoUpdate([
    { id: '1', content: 'A', status: 'completed' },
    { id: '2', content: 'B', status: 'completed' },
    { id: '3', content: 'C', status: 'cancelled' },
  ]));
  assert.ok(h);
  assert.match(h.text, /^✅ 计划 2 步全部完成$/, '取消项不应计入总数（否则会是 3）');
});

test('humanizeEvent: todo.update 单步计划不推送', () => {
  assert.equal(humanizeEvent(todoUpdate([{ id: '1', content: '就一件事', status: 'in_progress' }])), null);
});

test('humanizeEvent: todo.update 畸形载荷安全返回 null', () => {
  assert.equal(humanizeEvent({ name: 'todo.update', value: {} }), null);
  assert.equal(humanizeEvent({ name: 'todo.update', value: { items: 'not-an-array' } }), null);
  assert.equal(humanizeEvent({ name: 'todo.update', value: { items: [] } }), null);
  assert.equal(humanizeEvent({ name: 'todo.update', value: { items: [null, { status: 'pending' }] } }), null);
});

test('tracker: todo 相同完成数去重；计数变化的新文案受节流约束但仍记账', () => {
  const t = new ProgressTracker('wx-todo');
  const plan = todoUpdate([
    { id: '1', content: 'A', status: 'pending' },
    { id: '2', content: 'B', status: 'in_progress' },
  ]);
  assert.ok(t.ingest(plan), '首次计划应放行');
  assert.equal(t.ingest(plan), null, '相同计数应去重');
  // 完成数变化 → 新 key，但 20s 节流窗口内不放行（仍然记账，/进度 看得到）
  const progressed = todoUpdate([
    { id: '1', content: 'A', status: 'completed' },
    { id: '2', content: 'B', status: 'in_progress' },
  ]);
  assert.equal(t.ingest(progressed), null, '节流窗口内不放行');
  const texts = t.allEntries().map((e) => e.text);
  assert.ok(texts.some((x) => /✅ 1\/2/.test(x)), '进展文案应已记账');
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
