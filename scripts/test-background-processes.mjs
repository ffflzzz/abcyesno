/**
 * backgroundProcesses 纯逻辑单测
 *
 * 用法：node scripts/test-background-processes.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  partition,
  formatUptime,
  barLabel,
  finishedLabel,
  displayCommand,
  lastOutputLine,
  shortCommand,
  exitLabel,
  pickVisible,
} from '../src/utils/backgroundProcesses.js';

test('partition 按状态分组，未知状态算在跑', () => {
  const { running, finished } = partition([
    { session_id: 'a', status: 'running' },
    { session_id: 'b', status: 'exited', exit_code: 0 },
    { session_id: 'c' },
    null,
    'junk',
  ]);
  assert.deepEqual(running.map((p) => p.session_id), ['a', 'c']);
  assert.deepEqual(finished.map((p) => p.session_id), ['b']);
});

test('formatUptime 覆盖秒/分/小时三档', () => {
  assert.equal(formatUptime(0), '0 秒');
  assert.equal(formatUptime(42), '42 秒');
  assert.equal(formatUptime(59), '59 秒');
  assert.equal(formatUptime(60), '1 分钟');
  assert.equal(formatUptime(1380), '23 分钟');
  assert.equal(formatUptime(3900), '1 小时 5 分');
  assert.equal(formatUptime(7200), '2 小时');
  assert.equal(formatUptime(undefined), '0 秒');
  assert.equal(formatUptime(-9), '0 秒');
});

test('barLabel 取最长那个，单个也不省略时长', () => {
  assert.equal(barLabel([]), '');
  assert.equal(barLabel([{ uptime_seconds: 1380 }]), '1 个在跑 · 已 23 分钟');
  assert.equal(
    barLabel([{ uptime_seconds: 60 }, { uptime_seconds: 3900 }]),
    '2 个在跑 · 已 1 小时 5 分'
  );
});

test('lastOutputLine 跳过尾部空行并吃 CRLF', () => {
  assert.equal(lastOutputLine('第一行\r\n第二行\r\n\r\n'), '第二行');
  assert.equal(lastOutputLine('[16:06:10] 媒体链 status=incomplete'), '[16:06:10] 媒体链 status=incomplete');
  assert.equal(lastOutputLine(''), '');
  assert.equal(lastOutputLine('\n \n'), '');
  assert.equal(lastOutputLine(null), '');
});

test('shortCommand 压掉换行并按宽度截断', () => {
  assert.equal(shortCommand('python a.py\n  2>&1 | tee log'), 'python a.py 2>&1 | tee log');
  assert.equal(shortCommand('x'.repeat(100), 72).length, 72);
  assert.ok(shortCommand('x'.repeat(100), 72).endsWith('…'));
});

test('exitLabel 区分跑完 / 被终止 / 非零退出', () => {
  assert.equal(exitLabel({ status: 'exited', exit_code: 0 }), '跑完');
  assert.equal(exitLabel({ status: 'exited', exit_code: -15 }), '被终止');
  assert.equal(exitLabel({ status: 'exited' }), '被终止');
  assert.equal(exitLabel({ status: 'exited', exit_code: 1 }), '退出（码 1）');
  assert.equal(exitLabel({ status: 'running' }), '');
});

test('pickVisible 在跑的在前，刚退出的跟在后面', () => {
  const out = pickVisible([{ session_id: 'r1' }], [{ session_id: 'f1' }]);
  assert.deepEqual(out.map((p) => p.session_id), ['r1', 'f1']);
  assert.deepEqual(pickVisible([{ session_id: 'r1' }]), [{ session_id: 'r1' }]);
  assert.deepEqual(pickVisible(), []);
});

test('finishedLabel 把"跑完"和"挂了"分开说', () => {
  assert.equal(finishedLabel([]), '');
  assert.equal(finishedLabel([{ status: 'exited', exit_code: 0 }]), '后台任务已结束 · 跑完');
  assert.equal(finishedLabel([{ status: 'exited', exit_code: 1 }]), '后台任务已结束 · 退出（码 1）');
  assert.equal(finishedLabel([{ status: 'exited', exit_code: -15 }]), '后台任务已结束 · 被终止');
  assert.equal(
    finishedLabel([
      { status: 'exited', exit_code: 0 },
      { status: 'exited', exit_code: 1 },
    ]),
    '2 个后台任务已结束',
    '结果不一致时不硬凑一个结论'
  );
});

test('displayCommand 不截断，多行折成一行', () => {
  const cmd = 'cd "C:/x/shortdrama"\nrm -f .tmp/recover.log\npython .tmp/recover_v4.py 2>&1 | tee log';
  const out = displayCommand(cmd);
  assert.ok(out.includes('recover_v4.py'), '脚本名不能被截掉');
  assert.equal(out.split('\n').length, 1);
  assert.ok(out.includes('⏎'));
  assert.equal(displayCommand(''), '');
});
