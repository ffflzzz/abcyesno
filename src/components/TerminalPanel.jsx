import React, { useState, useEffect, useRef } from "react";

/**
 * TerminalPanel — 等宽终端风格输出面板
 * 背景 #1a1a2e，绿色文字，逐行追加，自动滚到底
 */
export default function TerminalPanel({ lines = [], isStreaming = false }) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines]);

  return (
    <div className="terminal-panel">
      <div className="terminal-body">
        {lines.map((line, i) => (
          <div key={i} className="terminal-line">
            {line}
          </div>
        ))}
        {isStreaming && <span className="terminal-cursor">▌</span>}
        <div ref={endRef} />
      </div>
    </div>
  );
}
