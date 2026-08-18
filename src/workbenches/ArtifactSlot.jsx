import React from "react";
import { toLoadableSrc } from "../utils/mediaSrc.js";

// Shared per-step artifact renderer used by every workbench (hand-written
// ManjuCraftWorkbench and the generic Blueprint/Timeline renderers). Renders
// an image / video / audio / file / text artifact the same way everywhere so
// "every step's output is observable" stays consistent across workflows.
export default function ArtifactSlot({ artifact }) {
  if (!artifact) {
    return <div className="wb-slot empty">暂无产物</div>;
  }
  // Sandboxed renderer blocks file:// media; route local paths through
  // abcyesno-local:// (idempotent for http/data/already-local URLs).
  const mediaSrc = toLoadableSrc(artifact.src);
  switch (artifact.type) {
    case "image":
      return mediaSrc ? (
        <img className="wb-slot-media" src={mediaSrc} alt={artifact.label} />
      ) : (
        <div className="wb-slot placeholder">
          <span>图片预览</span>
        </div>
      );
    case "video":
      return mediaSrc ? (
        <video className="wb-slot-media" src={mediaSrc} controls />
      ) : (
        <div className="wb-slot placeholder">
          <span>视频预览</span>
        </div>
      );
    case "audio":
      return mediaSrc ? (
        <audio className="wb-slot-media" src={mediaSrc} controls />
      ) : (
        <div className="wb-slot placeholder">
          <span>音频</span>
        </div>
      );
    case "file":
      return (
        <div className="wb-slot file">
          <span className="wb-file-icon">⤓</span>
          <span className="wb-file-name">{artifact.label}</span>
          {artifact.mime && <em className="wb-file-mime">{artifact.mime}</em>}
        </div>
      );
    case "text":
    default:
      return <div className="wb-slot text">{artifact.text || artifact.label}</div>;
  }
}
