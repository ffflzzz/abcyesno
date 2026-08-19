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
  // Normalize: backslashes → forward slashes, drop the colon after the drive
  // letter (e.g. "C:\Users\foo" → "C/Users/foo"). The colon MUST be stripped —
  // if we encodeURIComponent it as %3A, Chromium's URL parser decodes it back
  // to ":" and treats it as the authority/path separator, mangling the URL
  // (`abcyesno-local://c/Users/...` → handler sees no drive). Keep the rest
  // encoded so non-ASCII (Chinese filenames) survives.
  const normalized = src.replace(/\\/g, '/').replace(/^([A-Za-z]):/, '$1');
  // Encode each path segment so that spaces / Chinese / `#?%&` survive, but
  // leave the drive letter (`C`) untouched.
  const segments = normalized.split('/').map((seg, i) => (i === 0 ? seg : encodeURIComponent(seg))).join('/');
  return `abcyesno-local:///${segments}`;
}

export function originalPathOf(src) {
  if (!src || typeof src !== 'string') return src;
  if (src.startsWith('abcyesno-local://')) {
    try {
      // Strip both 2- and 3-slash prefixes uniformly.
      let body = src.replace(/^abcyesno-local:\/+\/?/, '');
      // Restore the drive-letter colon ("C/foo" → "C:/foo").
      body = body.replace(/^([A-Za-z])\//, '$1:/');
      // Decode the remaining encoded path segments (skip drive letter).
      const parts = body.split('/');
      const decoded = parts.map((seg, i) => (i === 0 ? seg : decodeURIComponent(seg))).join('/');
      return decoded;
    } catch (_) {
      return src;
    }
  }
  return src;
}
