import React, { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * TypewriterText — 逐字打字机效果
 * 字符间隔 15ms，Markdown 渲染，text_done 后停止光标闪烁
 */
export default function TypewriterText({
  content = "",
  speed = 15,
  isStreaming = false,
  onImageClick,
}) {
  const [displayedLength, setDisplayedLength] = useState(0);
  const [showCursor, setShowCursor] = useState(true);
  const intervalRef = useRef(null);
  const cursorRef = useRef(null);

  // Typewriter effect
  useEffect(() => {
    if (!isStreaming) {
      setDisplayedLength(content.length);
      return;
    }

    if (intervalRef.current) clearInterval(intervalRef.current);

    intervalRef.current = setInterval(() => {
      setDisplayedLength((prev) => {
        if (prev >= content.length) {
          clearInterval(intervalRef.current);
          return prev;
        }
        return prev + 1;
      });
    }, speed);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [content, isStreaming, speed]);

  // Cursor blink
  useEffect(() => {
    if (!isStreaming) {
      setShowCursor(false);
      return;
    }
    setShowCursor(true);
    cursorRef.current = setInterval(() => {
      setShowCursor((prev) => !prev);
    }, 500);
    return () => {
      if (cursorRef.current) clearInterval(cursorRef.current);
    };
  }, [isStreaming]);

  const displayed = content.slice(0, displayedLength);

  return (
    <div className="typewriter-text">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        className="message-markdown"
        components={{
          img: ({ node, ...props }) => (
            <img
              {...props}
              style={{ maxWidth: "100%", cursor: "pointer", borderRadius: "6px" }}
              onClick={() => onImageClick && onImageClick(props.src, props.alt)}
              alt={props.alt || ""}
            />
          ),
        }}
      >
        {displayed}
      </ReactMarkdown>
      {showCursor && isStreaming && <span className="typewriter-cursor">▌</span>}
    </div>
  );
}
