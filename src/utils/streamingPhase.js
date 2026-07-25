/**
 * 从 CopilotKit messages 推断流式阶段
 * @param {Array} messages - CopilotKit 消息数组
 * @param {boolean} isLoading - 是否正在流式
 * @returns {"idle"|"thinking"|"tool_executing"|"text_generating"}
 */
export function inferStreamingPhase(messages, isLoading) {
  if (!isLoading) return "idle";
  if (!messages || messages.length === 0) return "thinking";

  const lastMsg = messages[messages.length - 1];
  const role = lastMsg?.role;

  // 最后一个消息是 tool call（ActionExecutionMessage）
  if (role === "tool" || lastMsg?.isActionExecutionMessage?.()) {
    return "tool_executing";
  }

  // 最后一个消息是 assistant 且有内容
  if (role === "assistant") {
    const content = typeof lastMsg.content === "string"
      ? lastMsg.content
      : (lastMsg.content?.text || "");
    if (content && content.trim().length > 0) {
      return "text_generating";
    }
  }

  // assistant 消息存在但无内容 → 还在 thinking
  return "thinking";
}
