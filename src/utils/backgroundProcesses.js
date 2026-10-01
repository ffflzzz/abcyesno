/**
 * 后台进程状态的纯逻辑（常驻记号用）
 *
 * 病根（2026-10-01）：网关的后台进程登记表一直有 `process.list` 这个 RPC
 * （注释写着 "desktop status stack"，且已列入 _LONG_HANDLERS 走线程池、
 * 明确给前端轮询用），但桌面端从来没调用过 —— 全仓搜不到一个读取方。
 * 于是 agent 用 terminal 后台跑 60 分钟的活，界面上看不出来任何区别。
 *
 * 这里只放可单测的纯函数：分类、计时、措辞、取最后一行输出。
 */

/** 按运行状态分组；未知状态按运行中处理（宁可多显示，不漏报）。 */
export function partition(list) {
  const running = [];
  const finished = [];
  for (const p of list || []) {
    if (!p || typeof p !== "object") continue;
    if (p.status === "exited") finished.push(p);
    else running.push(p);
  }
  return { running, finished };
}

/** 秒 → 人话。60 内给秒，60 分钟内给分钟，再往上给"几小时几分"。 */
export function formatUptime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h} 小时 ${rem} 分` : `${h} 小时`;
}

/**
 * 常驻条主文案。
 * 例："1 个在跑 · 已 23 分钟" / "2 个在跑 · 最长 1 小时 5 分"
 */
export function barLabel(running) {
  const n = (running || []).length;
  if (n === 0) return "";
  const longest = Math.max(0, ...running.map((p) => Number(p.uptime_seconds) || 0));
  const head = n === 1 ? "1 个在跑" : `${n} 个在跑`;
  return `${head} · 已 ${formatUptime(longest)}`;
}

/**
 * 没有在跑的、只有"刚退出"的时候说什么。
 * 折叠态也必须交代结果 —— 只说"已结束"等于把"跑完"和"挂了"混成一个词，
 * 而这两件事用户需要分开看到（2026-10-01 那次就是：出片失败和出片成功，
 * 界面上都只会是"没动静"）。
 */
export function finishedLabel(finished) {
  const list = finished || [];
  if (list.length === 0) return "";
  const labels = [...new Set(list.map(exitLabel))].filter(Boolean);
  const head = list.length === 1 ? "后台任务已结束" : `${list.length} 个后台任务已结束`;
  return labels.length === 1 ? `${head} · ${labels[0]}` : head;
}

/** 展开态的完整命令：不截断，多行折成一行可读形式。 */
export function displayCommand(cmd) {
  return String(cmd || "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .join("  ⏎  ");
}

/** 取输出尾部最后一行非空文本 —— 心跳的可见内容就靠它。 */
export function lastOutputLine(tail) {
  if (typeof tail !== "string" || !tail.trim()) return "";
  const lines = tail
    .split(/\r?\n/)
    .map((l) => l.replace(/\s+$/, "").trimEnd())
    .filter((l) => l.trim().length > 0);
  return lines.length ? lines[lines.length - 1] : "";
}

/** 命令行太长时截断显示，保留可辨识的开头。 */
export function shortCommand(cmd, max = 72) {
  const oneLine = String(cmd || "").replace(/\s+/g, " ").trim();
  if (oneLine.length <= max) return oneLine;
  return `${oneLine.slice(0, max - 1)}…`;
}

/** 退出码措辞：0 说"跑完"，非 0 说"退出（码）"，被杀说"被终止"。 */
export function exitLabel(proc) {
  if (!proc || proc.status !== "exited") return "";
  const code = proc.exit_code;
  if (code === 0) return "跑完";
  if (code === -15 || code === null || code === undefined) return "被终止";
  return `退出（码 ${code}）`;
}

/**
 * 常驻条该显示哪些条目。
 * running 一律显示；justFinished 只在"这一轮刚从 running 掉出来"时保留，
 * 否则登记表里的历史进程会把条常驻撑满。
 */
export function pickVisible(running, justFinished) {
  return [...(running || []), ...(justFinished || [])];
}

/**
 * 距上次新输出多久了 —— 心跳的判据。
 *
 * 阈值为什么是 600 秒：短剧驱动一个正常阶段（reviewer 137-175s、scenedesigner
 * 611s）期间驱动本身不打新日志，输出尾行本来就会静止几分钟；把阈值定在 3 分钟
 * 会在每次正常运行时误报。10 分钟是"这一天里从没出现过这么长的静默"那一档。
 * 措辞只报事实（"已 N 分钟无新输出"），不下"卡住了"的判断。
 */
// 分成两行写而不是 `export const`：scripts/check-tdz.js 会把
// ExportNamedDeclaration 当成一次"先于声明的引用"，对 `export const` 误报
// TDZ。别合并回去，否则守卫会红。
const STALE_AFTER_SECONDS = 600;
export { STALE_AFTER_SECONDS };

export function silentLabel(silentSeconds) {
  const s = Math.max(0, Math.floor(Number(silentSeconds) || 0));
  if (s < 60) return "刚刚有输出";
  if (s < 3600) return `已 ${Math.floor(s / 60)} 分钟无新输出`;
  return `已 ${formatUptime(s)}无新输出`;
}

export function isStale(silentSeconds) {
  return (Number(silentSeconds) || 0) >= STALE_AFTER_SECONDS;
}

/**
 * 折叠态该拿哪个进程的输出当"最新一行"。
 * 取"最近还在动"的那个（静默秒数最小），而不是跑得最久的 —— 后者会一直显示
 * 一个早已不动的进程的尾巴。
 */
export function pickLiveliest(list, silentOf) {
  const rows = (list || []).filter(Boolean);
  if (rows.length === 0) return null;
  return rows.reduce((best, p) =>
    silentOf(p) < silentOf(best) ? p : best
  , rows[0]);
}
