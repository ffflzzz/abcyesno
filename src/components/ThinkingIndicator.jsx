import React from "react";

/**
 * ThinkingIndicator — 思考状态指示器
 * 波纹呼吸动画 + 灰色文字 + 三个点逐个弹跳（0/150/300ms delay）
 */
export default function ThinkingIndicator({ text = "Thinking" }) {
  return (
    <div className="thinking-indicator">
      <span className="thinking-spinner" aria-hidden="true" />
      <span className="thinking-text">{text}</span>
      <span className="thinking-dots">
        <span className="thinking-dot" style={{ animationDelay: "0ms" }}>.</span>
        <span className="thinking-dot" style={{ animationDelay: "150ms" }}>.</span>
        <span className="thinking-dot" style={{ animationDelay: "300ms" }}>.</span>
      </span>
    </div>
  );
}
