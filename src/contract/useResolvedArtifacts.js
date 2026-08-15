import { useEffect, useRef, useState } from "react";

// Extract a local filesystem path from an artifact descriptor, or null if the
// artifact is a remote (http/data) URL that the renderer can load directly.
export function localPathOf(a) {
  if (!a) return null;
  if (a.path) return a.path;
  const s = a.src || a.url || "";
  if (/^file:\/\//i.test(s)) {
    let fp = s.replace(/^file:\/\//i, "");
    if (/^\/[A-Za-z]:/.test(fp)) fp = fp.slice(1);
    return fp;
  }
  if (/^(https?:|data:)/i.test(s)) return null; // remote — no IPC needed
  return s || null;
}

// Resolve local-path artifacts to data URLs via the main-process IPC so the
// sandboxed renderer can display images it cannot load via file:// directly
// (Electron renderer security policy). Remote http/data URLs are returned as
// the source and never go through IPC.
export function useResolvedArtifacts(artifacts) {
  const [resolved, setResolved] = useState({});
  const resolvedRef = useRef({});
  const failedRef = useRef({});

  // Stable signature of the local paths so the effect only re-runs when the
  // set of artifacts actually changes (not on every parent re-render, since
  // the artifacts array identity changes each render).
  const sig = (artifacts || [])
    .map((a, i) => {
      const key = a.id || a.label || `a${i}`;
      return `${key}:${localPathOf(a) || "remote"}`;
    })
    .join("|");

  useEffect(() => {
    if (!artifacts || !artifacts.length) return undefined;
    let cancelled = false;
    artifacts.forEach((a, i) => {
      const key = a.id || a.label || `a${i}`;
      const lp = localPathOf(a);
      if (!lp) return; // remote URL, rendered directly
      if (resolvedRef.current[key] || failedRef.current[key]) return;
      const api = typeof window !== "undefined" && window.hermes;
      if (!api || !api.readLocalImage) return;
      api
        .readLocalImage(lp)
        .then((r) => {
          if (cancelled) return;
          if (r && r.dataUrl) {
            resolvedRef.current[key] = r.dataUrl;
            setResolved((prev) => ({ ...prev, [key]: r.dataUrl }));
          } else {
            failedRef.current[key] = true;
          }
        })
        .catch(() => {
          if (!cancelled) failedRef.current[key] = true;
        });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);

  return resolved;
}
