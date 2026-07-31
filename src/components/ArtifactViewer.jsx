import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// ArtifactViewer — §4.3 type dispatcher for the ResultPanel.
// Renders a single contract artifact by type. Read-only previews only
// (no agent navigation / form fills) to keep the safety boundary from §8.
//
// NOTE: this is a NEW component distinct from the chat's ArtifactPreview.jsx
// (which extracts images from tool messages). Keeping them separate avoids
// regressing the chat rendering.

function srcFor(artifact) {
  const p = artifact || {};
  if (p.source === "url") return p.url;
  if (p.path) return "file://" + p.path.replace(/\\/g, "/");
  return p.inline || null;
}

function isMarkdown(a) {
  const s = (a.path || a.label || "").toLowerCase();
  return /\.md$|\.markdown$/.test(s);
}

export default function ArtifactViewer({ artifact, onOpenExternal }) {
  const a = artifact || {};
  const type = a.type || "file";
  const src = srcFor(a);
  const [imgError, setImgError] = useState(false);

  // Guard against internal/unsafe protocols leaking into href or shell.openExternal
  const isSafeUrl = (u) => /^(https?:|file:|data:image\/)/i.test(u || "");

  if (type === "image" && src) {
    if (imgError) {
      return (
        <div className="artifact-file-view">
          <div className="artifact-label">{a.label || "图片"}</div>
          <div className="artifact-missing">图片无法加载</div>
          {isSafeUrl(src) && (
            <div className="artifact-file-actions">
              <a className="artifact-download" href={src} download target="_blank">下载</a>
              {onOpenExternal && <button className="artifact-open-ext" onClick={() => onOpenExternal(src)}>外开</button>}
            </div>
          )}
        </div>
      );
    }
    return <img className="artifact-full-img" src={src} alt={a.label || "image"} onError={() => setImgError(true)} />;
  }
  if (type === "video" && src) {
    return <video className="artifact-full-video" src={src} controls />;
  }
  if (type === "audio" && src) {
    return <audio className="artifact-full-audio" src={src} controls />;
  }
  if (type === "text") {
    if (isMarkdown(a)) {
      return (
        <div className="artifact-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{a.text || ""}</ReactMarkdown>
        </div>
      );
    }
    return <pre className="artifact-text">{a.text || ""}</pre>;
  }
  if (type === "file") {
    const name = (a.path || a.label || "").toLowerCase();
    const isPdf = /\.pdf$/.test(name);
    const isHtml = /\.html?$/.test(name);
    // PDF / HTML use the read-only Chromium <webview> (no extra deps).
    if ((isPdf || isHtml) && src) {
      return (
        <webview
          className="artifact-webview"
          src={src}
          partition="isolated-result"
          webpreferences="contextIsolation=true"
        />
      );
    }
    return (
      <div className="artifact-file-view">
        <div className="artifact-label">{a.label || a.path || "文件"}</div>
        {a.mime && <div className="artifact-sub">{a.mime}</div>}
        <div className="artifact-file-actions">
          {isSafeUrl(src) && (
            <a className="artifact-download" href={src} download>
              下载
            </a>
          )}
          {isSafeUrl(src) && onOpenExternal && (
            <button className="artifact-open-ext" onClick={() => onOpenExternal(src)}>
              外开
            </button>
          )}
        </div>
      </div>
    );
  }
  return <div className="artifact-missing">不支持的预览类型：{type}</div>;
}
