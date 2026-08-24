/**
 * test-wechat-provider.mjs — 微信桥接 provider 回归
 *
 * 回归目标：provider 之前把 fetch 返回的 Web ReadableStream 当成 Node 流
 * 使用（setEncoding / .on('data')），在 Node 下抛
 * "nodeStream.setEncoding is not a function"。本测试启动一个本地 SSE mock
 * server 返回 AG-UI 事件帧，直接调用 claudeQuery，验证：
 *   1. 不再报 setEncoding 类错误
 *   2. 能正确解析 TEXT_MESSAGE_CONTENT 聚合文本
 *   3. onText / onTurnEnd 回调按预期触发
 *
 * 不依赖真实 agui-server、不连 iLink。数据目录隔离到临时目录。
 */
import { createRequire } from 'node:module';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wcc-prov-'));
process.env.WCC_DATA_DIR = tmp;

const require = createRequire(import.meta.url);
const { claudeQuery } = require('../electron/backend/wechat_bridge/dist/index.js');

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

function startMockServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      if (req.method !== 'POST') {
        res.writeHead(405);
        res.end();
        return;
      }
      req.on('data', () => {}); // drain
      req.on('end', () => {
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        });
        const frames = [
          { type: 'TEXT_MESSAGE_START', messageId: 'm1' },
          { type: 'TEXT_MESSAGE_CONTENT', delta: '你好' },
          { type: 'TEXT_MESSAGE_CONTENT', delta: '，微信桥接已打通' },
          { type: 'RUN_FINISHED' },
        ];
        for (const f of frames) {
          res.write(`data: ${JSON.stringify(f)}\n\n`);
        }
        res.end();
      });
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port });
    });
  });
}

async function main() {
  const { server, port } = await startMockServer();
  process.env.AGUI_PORT = String(port);

  let captured = '';
  let turnEnd = '';
  const result = await claudeQuery({
    prompt: 'hello',
    threadId: 'wx-test',
    onText: (t) => {
      captured += t;
    },
    onTurnEnd: (r) => {
      turnEnd = r;
    },
  });

  check('claudeQuery 不抛 setEncoding 错误', !result.error || !/setEncoding/.test(result.error || ''), `error=${result.error}`);
  check('解析出文本内容', result.text.includes('微信桥接已打通'), `text=${JSON.stringify(result.text)}`);
  check('onText 累积文本与结果一致', captured === result.text, `captured=${JSON.stringify(captured)}`);
  check('onTurnEnd 收到 end_turn', turnEnd === 'end_turn', `turnEnd=${turnEnd}`);
  check('无 error 字段', !result.error, `error=${result.error}`);

  server.close();
  console.log(`\n结果: ${pass} passed, ${fail} failed`);
  try {
    fs.rmSync(tmp, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error('测试异常:', e);
  try {
    fs.rmSync(tmp, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
  process.exit(2);
});
