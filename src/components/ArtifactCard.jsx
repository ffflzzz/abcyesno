import React from "react";
import Icon from "./Icon.jsx";

// Generic artifact renderer (L3). Switches on artifact.type only - no
// per-workflow branch. Local paths are served via file:// so the Electron
// renderer can open artifacts written by the LangGraph workflow.
function srcFor(artifact) {
  const p = artifact || {};
  if (p.source === "url") return p.url;
  if (p.path) return "file://" + p.path.replace(/\\/g, "/");
  return p.inline || "";
}

export default function ArtifactCard({ artifact }) {
  const a = artifact || {};
  const type = a.type || "file";
  const src = srcFor(a);
  const isSafeUrl = (u) => /^(https?:|file:|data:image\/)/i.test(u || "");

  if (type === "video") {
    return (
      <div className="artifact-card">
        <div className="artifact-label">{a.label || "视频"}</div>
        {src ? (
          <video className="artifact-video" src={src} controls />
        ) : (
          <div className="artifact-missing">无可用视频源</div>
        )}
      </div>
    );
  }
  if (type === "image") {
    return (
      <div className="artifact-card">
        <div className="artifact-label">{a.label || "图片"}</div>
        {src ? (
          <img className="artifact-img" src={src} alt={a.label || "image"} />
        ) : (
          <div className="artifact-missing">无可用图片源</div>
        )}
      </div>
    );
  }
  if (type === "file") {
    return (
      <div className="artifact-card artifact-file">
        <span className="artifact-icon"><Icon name="file" size={16} /></span>
        <div className="artifact-meta">
          <div className="artifact-label">{a.label || "文件"}</div>
          {a.mime ? <div className="artifact-sub">{a.mime}</div> : null}
        </div>
        {a.path && isSafeUrl(src) ? (
          <a className="artifact-download" href={src} download>
            下载
          </a>
        ) : null}
      </div>
    );
  }
  return (
    <div className="artifact-card">
      <div className="artifact-label">{a.label || type}</div>
      {src && isSafeUrl(src) ? (
        <a href={src} target="_blank" rel="noreferrer">
          打开
        </a>
      ) : null}
    </div>
  );
}
