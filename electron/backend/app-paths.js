// electron/backend/app-paths.js
// asar 感知的 app 资源路径 helper（2026-09-21 打包瘦身改造的配套件）。
//
// 背景：打包改为 asar: true 后，electron/ 与 node_modules/ 收进
// resources/app.asar，而 hermes-fork/、shortdrama/、bin/ 三个目录因为
// 要被 Python 解释器 / spawn 直接读真实磁盘文件，被 asarUnpack 到
// resources/app.asar.unpacked/ 下。
//
// Node 侧 fs/require 可以透明读 asar，但 child_process.spawn 的
// python.exe（venv）只认真实磁盘路径，所以所有指向这三个目录的路径
// 都必须做 app.asar → app.asar.unpacked 替换。
//
// dev 模式路径里没有 app.asar 段，原样返回 —— 开发流程零影响。
const path = require('path');

const ASAR_SEG = `${path.sep}app.asar${path.sep}`;
const UNPACKED_SEG = `${path.sep}app.asar.unpacked${path.sep}`;

/**
 * 把 asar 虚拟路径替换为 unpacked 真实磁盘路径。
 * - 打包环境：...resources\app.asar\hermes-fork → ...resources\app.asar.unpacked\hermes-fork
 * - dev 环境：路径不含 app.asar 段，原样返回
 * - 对已在 app.asar.unpacked 下的路径幂等（`\app.asar\` 不匹配 `\app.asar.unpacked\`）
 */
function unpacked(p) {
  if (typeof p !== 'string') return p;
  if (!p.includes(ASAR_SEG)) return p;
  return p.replace(ASAR_SEG, UNPACKED_SEG);
}

module.exports = { unpacked };
