import React from "react";
import Icon from "./Icon.jsx";
import { useResolvedArtifacts, localPathOf } from "../contract/useResolvedArtifacts.js";
import { toLoadableSrc } from "../utils/mediaSrc.js";

// Generic artifact renderer (L3). Switches on artifact.type only - no
// per-workflow branch. Local paths are resolved to data URLs via the main
// process (readLocalImage IPC) so the sandboxed renderer can display images
// written by the LangGraph workflow - the renderer cannot load file:// directly.
// The IPC data-URL path handles images; for video (too large for base64) we
// fall back to abcyesno-local:// which the sandboxed renderer can stream.
function fileUrlFor(artifact) {
  const p = artifact || {};
  if (p.source === "url") return p.url;
  if (p.path) return toLoadableSrc(p.path);
  return p.inline || "";
}

export default function ArtifactCard({ artifact }) {
  const a = artifact || {};
  const resolved = useResolvedArtifacts([a]);
  const localKey = a.id || a.label || "a0";
  const resolvedSrc = resolved[localKey];
  const isLocal = !!localPathOf(a);
  const type = a.type || "file";
  // Prefer the IPC-resolved data URL; fall back to file:// so remote/legacy
  // paths still work (and nothing regresses if IPC is unavailable).
  const src = isLocal ? resolvedSrc || fileUrlFor(a) : fileUrlFor(a);
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
