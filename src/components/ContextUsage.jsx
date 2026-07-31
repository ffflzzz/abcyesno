import { useState, useMemo, useCallback } from "react";
import Icon from "./Icon";

/**
 * ContextUsage — 上下文用量统计面板（modal）
 *
 * 显示当前会话的 token 用量估算：
 * - 总用量百分比 + 已用/总量绝对值
 * - 渐变进度条
 * - 分类占比：系统提示词 / 工具及子智能体 / 对话消息 / 连接器MCP / 技能
 *
 * 数据来源：前端从 message history 估算（char ÷ ratio → token），
 * 后端暂无分类拆分 API，后续可对接 _build_usage_update 精确数据。
 */

// 模型上下文窗口大小（tokens），后续可从模型配置动态读取
const MODEL_CONTEXT_WINDOWS = {
  "agnes-2.5-flash": 128_000,
  "agnes-2.0-flash": 128_000,
  "agnes-2.0-pro": 128_000,
  "default": 128_000,
};

// 中英混合文本粗略 token 比：~3.5 字符/token（中文约 1.5-2 token/字，英文 ~4 字符/token）
const CHARS_PER_TOKEN = 3.5;

// 图片固定 token 开销（每张）
const IMAGE_TOKEN_COST = 1500;

/**
 * 从消息列表估算各分类 token 用量
 */
function estimateUsageBuckets(messages = [], modelName = "default") {
  const windowSize = MODEL_CONTEXT_WINDOWS[modelName] || MODEL_CONTEXT_WINDOWS["default"];

  let systemTokens = 0;     // 系统提示词（估算固定值，Hermes system prompt 通常 2K-8K tokens）
  let messageTokens = 0;    // 对话消息（user + assistant 内容）
  let toolTokens = 0;       // 工具调用（tool call 参数 + 结果）
  let mcpTokens = 0;        // MCP 连接器调用
  let skillTokens = 0;      // Skill 相关

  // 系统 prompt 估算：Hermes 的 system prompt 较长（含工具定义、skill 描述等），
  // 取保守估计 ~4000 tokens（~14K chars）。后续如后端暴露真实值可替换。
  systemTokens = 4000;

  for (const msg of messages) {
    const role = msg.role || "";
    const content = typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content || "");
    const toolName = msg.toolName || msg.name || "";
    const isToolCall = role === "tool" || !!msg.toolCallId || !!msg.toolUseId;
    const isToolResult = msg.type === "tool_result" || !!msg.toolResult;

    // 图片内容检测
    let imageCount = 0;
    if (Array.isArray(msg.content)) {
      for (const part of msg.content) {
        if (part?.type === "image" || part?.type === "image_url") imageCount++;
      }
    }

    // 文本 token 估算
    const textTokens = Math.ceil((content.length + 3) / CHARS_PER_TOKEN);
    const imgTokens = imageCount * IMAGE_TOKEN_COST;

    if (isToolCall || isToolResult) {
      // 工具调用归类
      const rawTokens = textTokens + imgTokens;
      if (toolName && (toolName.includes("mcp__") || toolName.includes("mcp-"))) {
        mcpTokens += rawTokens;
      } else {
        toolTokens += rawTokens;
      }
    } else if (role === "user" || role === "assistant" || role === "system") {
      // 对话消息
      messageTokens += textTokens + imgTokens;

      // 检测 skill 标记（Hermes 在 assistant 消息中可能带 skill 元信息）
      if (msg._skillId || msg.skillId || (typeof content === "string" && content.includes("[Skill]"))) {
        skillTokens += Math.min(textTokens, 500); // skill 元信息通常不长
      }
    } else {
      // 其他归入对话消息
      messageTokens += textTokens + imgTokens;
    }
  }

  const totalUsed = systemTokens + messageTokens + toolTokens + mcpTokens + skillTokens;
  const total = Math.min(totalUsed, windowSize); // 不超过窗口上限
  const pct = total > 0 ? ((totalUsed / windowSize) * 100) : 0;

  return {
    total: windowSize,
    used: Math.min(totalUsed, windowSize),
    percentage: Math.min(pct, 100),
    buckets: [
      { key: "system", label: "系统提示词", tokens: systemTokens, color: "#10b981" },
      { key: "tools", label: "工具及子智能体", tokens: toolTokens, color: "#f59e0b" },
      { key: "messages", label: "对话消息", tokens: messageTokens, color: "#8b5cf6" },
      { key: "mcp", label: "连接器及MCP", tokens: mcpTokens, color: "#06b6d4" },
      { key: "skills", label: "技能", tokens: skillTokens, color: "#3b82f6" },
    ],
  };
}

export default function ContextUsage({ messages = [], model = "agnes-2.5-flash", open, onClose }) {
  const data = useMemo(() => estimateUsageBuckets(messages, model), [messages, model]);

  const formatTokens = (n) => {
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return `${n}`;
  };

  const handleBackdropClick = useCallback((e) => {
    if (e.target === e.currentTarget) onClose();
  }, [onClose]);

  if (!open) return null;

  const { total, used, percentage, buckets } = data;

  return (
    <div className="context-usage-backdrop" onClick={handleBackdropClick}>
      <div className="context-usage-modal">
        {/* Header */}
        <div className="context-usage-header">
          <span className="context-usage-title">上下文用量</span>
          <button className="context-usage-close" onClick={onClose} title="关闭">
            <Icon name="close" size={16} />
          </button>
        </div>

        {/* Main stat */}
        <div className="context-usage-main">
          <span className="context-usage-pct">{percentage.toFixed(1)}%</span>
          <span className="context-usage-detail">已使用 {formatTokens(used)}/{formatTokens(total)}</span>
        </div>

        {/* Progress bar */}
        <div className="context-usage-bar-wrap">
          <div
            className="context-usage-bar"
            style={{
              width: `${Math.min(percentage, 100)}%`,
              background: percentage > 90
                ? "linear-gradient(90deg, #ef4444, #f97316)"
                : percentage > 70
                  ? "linear-gradient(90deg, #f59e0b, #eab308)"
                  : "linear-gradient(90deg, #10b981, #06b6d4, #8b5cf6)",
            }}
          />
        </div>

        {/* Category breakdown */}
        <div className="context-usage-buckets">
          {buckets.map((b) => {
            const bpct = used > 0 ? ((b.tokens / used) * 100) : 0;
            return (
              <div className="context-usage-bucket-row" key={b.key}>
                <span
                  className="context-usage-bucket-dot"
                  style={{ background: b.color }}
                />
                <span className="context-usage-bucket-label">{b.label}</span>
                <span className="context-usage-bucket-pct">{bpct.toFixed(1)}%</span>
              </div>
            );
          })}
        </div>

        {/* Footer hint */}
        <div className="context-usage-footer">
          估算值 · 实际用量以模型返回为准
        </div>
      </div>
    </div>
  );
}
