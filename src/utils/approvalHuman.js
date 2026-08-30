// approvalHuman.js — 审批弹窗可读性助手（2026-08-30）
//
// 普通用户无法从 30 行 `python -c` 判断安全性。弹窗主角必须是「agent 想做
// 什么」的一句人话（后端 approval.request 现在带 summary 字段，这里是前端
// 兜底镜像），原始命令降级为默认折叠的技术详情。

/** 从命令推测一句中文摘要（与后端 _summarize_command_for_human 同型启发式）。 */
export function summarizeCommand(command) {
  try {
    const cmd = String(command || "").trim();
    if (!cmd) return "";
    const firstLine = cmd.includes("\n") ? cmd.split("\n")[0].trim() : cmd;
    const low = cmd.toLowerCase();

    if (/python[0-9.]*\s+(-u\s+)?-c\s/.test(low)) {
      const codeM = cmd.match(/python[0-9.]*\s+(-u\s+)?-c\s+["'](.*)["']\s*$/s);
      const code = codeM ? codeM[2] : "";
      const nLines = Math.max(1, code.split("\n").filter((l) => l.trim()).length);
      const tM = code.match(/["']([^"']+\.(?:json|md|txt|csv|ya?ml|html|js|py|srt))["']/i);
      const target = tM ? `，读写 ${tM[1]}` : "";
      return `在工作区运行 Python 内联脚本（约 ${nLines} 行，用于更新任务状态）${target}`;
    }
    const clone = low.match(/git\s+clone\s+(\S+)/);
    if (clone) return `克隆 Git 仓库：${clone[1]}`;
    if (/\brm\s+-[rf]/.test(low)) {
      const p = cmd.match(/["']?([/~][^\s"';&|]+)/);
      return `递归删除文件/目录：${p ? p[1] : firstLine.slice(0, 80)}`;
    }
    if (/curl|[^\w]wget/.test(low) && /\|\s*(ba)?sh/.test(low)) return "下载并直接执行远程脚本（高危操作）";
    const pip = low.match(/pip[0-9.]*\s+install\s+(.+)/);
    if (pip) {
      const pkgs = pip[1].split(/\s+/).filter((p) => !p.startsWith("-")).slice(0, 4);
      return `安装 Python 依赖包：${pkgs.join(", ")}`;
    }
    if (/\bnpm\s+(i|install)\b/.test(low)) return "安装 npm 依赖包";
    if (/^git\s/.test(low)) {
      const op = low.split(/\s+/)[1] || "操作";
      return `执行 Git ${op} 操作`;
    }
    if (/(curl|[^\w]wget)/.test(low)) return "从网络下载内容";
    return firstLine.slice(0, 100);
  } catch {
    return "";
  }
}

/**
 * 弹窗顶部的一句人话。优先级：后端 summary > 前端启发式 > description
 * （英文模式描述，仅作最后兜底）。
 */
export function humanSummaryOf(approval) {
  if (!approval) return "";
  return String(approval.summary || "").trim()
    || summarizeCommand(approval.command || approval.args)
    || String(approval.description || "").trim();
}

/**
 * 操作类型的人话名称。工具授权的 operation 常为空（弹「未知操作」），
 * 从命令前缀推测；workflow 门保留原有 label/operation。
 */
export function friendlyOperationOf(approval) {
  if (!approval) return "";
  if (approval.source === "workflow") {
    return approval.label || approval.operation || "工作流确认";
  }
  // 工具授权：命令前缀推测优先（operation 常为空 → 弹「未知操作」）
  const cmd = String(approval.command || "").trim().toLowerCase();
  if (cmd) {
    if (/python[0-9.]*\s+(-u\s+)?-c\s/.test(cmd)) return "运行 Python 脚本";
    if (/^git\s/.test(cmd)) return "Git 操作";
    if (/\brm\s+-[rf]/.test(cmd)) return "删除文件";
    if (/(curl|[^\w]wget)/.test(cmd)) return "网络访问";
    if (/\bnpm\s|\bpip[0-9.]*\s+install\b/.test(cmd)) return "安装依赖";
    if (/\b(mkdir|mv|cp|touch|echo\s*>)/.test(cmd)) return "文件写入";
    return "运行终端命令";
  }
  if (approval.label) return approval.label;
  if (approval.operation && approval.operation !== "unknown") return approval.operation;
  return "运行终端命令";
}
