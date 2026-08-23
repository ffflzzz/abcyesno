import { homedir } from 'node:os';
import { join } from 'node:path';

/**
 * Data directory resolution order:
 * 1. WCC_DATA_DIR env (set by abcyesno's wechat-bridge-runner.js)
 * 2. %HERMES_HOME%/wechat_bridge  (abcyesno portable data root)
 * 3. ~/.wechat-claude-code        (upstream default, kept for standalone use)
 */
function resolveDataDir(): string {
  if (process.env.WCC_DATA_DIR) return process.env.WCC_DATA_DIR;
  const hermesHome = process.env.HERMES_HOME;
  if (hermesHome) return join(hermesHome, 'wechat_bridge');
  return join(homedir(), '.wechat-claude-code');
}

export const DATA_DIR = resolveDataDir();

export const DEFAULT_WORKING_DIR = join(homedir(), 'Documents', 'ClaudeCode');

export const CDN_BASE_URL = 'https://novac2c.cdn.weixin.qq.com/c2c';
