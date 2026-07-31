import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * UiMarkdown — 受控 markdown 渲染器，供 ui 组件复用。
 * 安全：不渲染原始 HTML（react-markdown 默认不渲染 HTML 字符串），
 * 不引入任意组件，仅结构化 markdown（spec §6.3）。
 */
export default function UiMarkdown({ children, className = "" }) {
  const md = typeof children === "string" ? children : "";
  if (!md.trim()) return null;
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      className={`ui-md ${className}`}
      components={{
        a: ({ node, ...props }) => (
          <a {...props} target="_blank" rel="noopener noreferrer" />
        ),
      }}
    >
      {md}
    </ReactMarkdown>
  );
}
