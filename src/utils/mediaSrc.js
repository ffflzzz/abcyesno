// src/utils/mediaSrc.js
//
// The renderer is sandboxed (opaque origin) and CANNOT load file:// media
// subresources across directories. Backend / workflow artifacts come back as
// local filesystem paths, so every component that renders local workspace
// media (images / video / audio) must route them through the privileged custom
// protocol `abcyesno-local://` registered in electron/main.js — NOT `file://`.
//
// This helper is the single source of truth. It is idempotent: http(s)://,
// data: and already-abcyesno-local URLs pass through untouched; a bare local
// path is encoded into abcyesno-local:// so the sandboxed renderer can load it.
//
// Imported by ArtifactSlot / ArtifactCard / ArtifactViewer / ArtifactPreview /
// ApprovalBubble / ResultPanel (and mirrored by StudioWorkbench's local copy).

export function isRemoteMediaUrl(src) {
  if (!src || typeof src !== 'string') return false;
  return /^(https?:|data:|abcyesno-local:)/i.test(src);
}

export function toLoadableSrc(src) {
  if (!src || typeof src !== 'string') return src;
  if (isRemoteMediaUrl(src)) return src;
  const normalized = src.replace(/\\/g, '/');
  return `abcyesno-local://${encodeURIComponent(normalized)}`;
}

export function originalPathOf(src) {
  if (!src || typeof src !== 'string') return src;
  if (src.startsWith('abcyesno-local://')) {
    try {
      return decodeURIComponent(src.replace('abcyesno-local://', ''));
    } catch (_) {
      return src;
    }
  }
  return src;
}
