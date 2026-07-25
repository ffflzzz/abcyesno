import React, { useState, useMemo } from "react";

/**
 * ArtifactPreview — 实时产物预览
 *
 * 当工具调用结果包含图片数据（base64 或 URL）时，立即展示预览卡片。
 * 用于 manju_craft 等工作流的风格确认、图片生成结果等 human-in-the-loop 场景。
 *
 * Props:
 *   - toolMessages: tool role 消息数组（从中提取含图片的结果）
 *   - onImageClick: 点击图片回调 (src, alt) => void
 */

const IMAGE_RE = /(?:^|[^\w])(data:image\/[a-zA-Z0-9+]+;base64,[A-Za-z0-9+/=]+|https?:\/\/[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp)[^\s"')]*|[A-Za-z]:\\[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp)|\/?(?:[^\s"':]+\/)*[^\s"':]+\.(?:png|jpe?g|gif|svg|webp|bmp))(?:[^\w]|$)/i;

function looksLikeImageUrl(str) {
  if (!str || typeof str !== "string") return false;
  const trimmed = str.trim();
  if (/^data:image\//i.test(trimmed)) return true;
  if (/^https?:\/\/.+\.(?:png|jpe?g|gif|svg|webp|bmp)/i.test(trimmed)) return true;
  if (/\.(?:png|jpe?g|gif|svg|webp|bmp)(\?.*)?$/i.test(trimmed)) return true;
  return false;
}

/** 把本地文件路径转为可渲染 URL */
function normalizeImageUrl(str) {
  if (!str) return null;
  const trimmed = str.trim();
  if (/^data:image\//i.test(trimmed)) return trimmed;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  // Windows / Unix absolute path → file://
  if (/^[A-Za-z]:\\/.test(trimmed) || /^\//.test(trimmed)) {
    return "file://" + trimmed.replace(/\\/g, "/");
  }
  return trimmed;
}

/** 递归从任意 result 结构中提取所有图片 URL */
function extractImagesFromValue(value, found = []) {
  if (typeof value === "string") {
    if (looksLikeImageUrl(value)) {
      const url = normalizeImageUrl(value);
      if (url && !found.includes(url)) found.push(url);
    } else {
      // Also scan inside plain text for embedded URLs
      let m;
      const re = /(data:image\/[a-zA-Z0-9+]+;base64,[A-Za-z0-9+/=]+|https?:\/\/[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp)[^\s"')]*|[A-Za-z]:\\[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp))/gi;
      while ((m = re.exec(value)) !== null) {
        const url = normalizeImageUrl(m[1]);
        if (url && !found.includes(url)) found.push(url);
      }
    }
  } else if (Array.isArray(value)) {
    value.forEach(v => extractImagesFromValue(v, found));
  } else if (value && typeof value === "object") {
    // Prioritize likely image keys
    const priorityKeys = ["image", "image_url", "url", "path", "file", "src", "preview", "thumbnail", "frames"];
    priorityKeys.forEach(k => {
      if (value[k] !== undefined) extractImagesFromValue(value[k], found);
    });
    // Then scan remaining keys
    Object.values(value).forEach(v => extractImagesFromValue(v, found));
  }
  return found;
}

/** Extract a short label from tool name + args */
function extractLabel(toolName, args) {
  let label = toolName || "产物";
  const argsSnippet = (args || "").replace(/\s+/g, " ").slice(0, 40);
  if (argsSnippet && argsSnippet !== "{}") {
    label += `: ${argsSnippet}`;
  }
  return label;
}

export default function ArtifactPreview({ toolMessages = [], onImageClick }) {
  const [lightbox, setLightbox] = useState(null);

  const artifacts = useMemo(() => {
    const items = [];
    for (const m of toolMessages) {
      const content = m.result !== undefined ? m.result : m.content;
      const urls = extractImagesFromValue(content, []);
      for (const url of urls) {
        if (!items.find(a => a.url === url)) {
          items.push({
            id: m.id || `artifact-${items.length}`,
            url,
            label: extractLabel(m.toolName, m.args),
            status: m.status,
            createdAt: m.createdAt,
          });
        }
      }
      // Also check chunks array for streaming image data
      if (Array.isArray(m.chunks)) {
        for (const chunk of m.chunks) {
          const chunkUrls = extractImagesFromValue(chunk, []);
          for (const url of chunkUrls) {
            if (!items.find(a => a.url === url)) {
              items.push({
                id: `${m.id}-chunk-${items.length}`,
                url,
                label: extractLabel(m.toolName, m.args),
                status: m.status,
                createdAt: m.createdAt,
                isChunk: true,
              });
            }
          }
        }
      }
    }
    return items;
  }, [toolMessages]);

  if (artifacts.length === 0) return null;

  return (
    <div className="artifact-preview">
      <div className="artifact-preview-title">产物预览</div>
      <div className="artifact-grid">
        {artifacts.map((a) => (
          <div key={a.id} className={`artifact-card artifact-${a.status || "done"}`}>
            <img
              className="artifact-img"
              src={a.url}
              alt={a.label}
              loading="lazy"
              onClick={() => {
                if (onImageClick) onImageClick(a.url, a.label);
                else setLightbox(a);
              }}
            />
            <div className="artifact-label">{a.label}</div>
            {a.status === "running" && (
              <div className="artifact-overlay">
                <span className="thinking-spinner" />
                <span>生成中…</span>
              </div>
            )}
          </div>
        ))}
      </div>
      {lightbox && (
        <div className="artifact-lightbox-mask" onClick={() => setLightbox(null)}>
          <img
            className="artifact-lightbox-img"
            src={lightbox.url}
            alt={lightbox.label}
            onClick={(e) => e.stopPropagation()}
          />
          <div className="artifact-lightbox-caption">{lightbox.label}</div>
        </div>
      )}
    </div>
  );
}
