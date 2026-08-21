// Agnes AI media generation client (Node side).
// Used by the Studio workbench routes in agui-server.js. The API key is read
// from HERMES_HOME/.env (same source as the transcribe route) or AGNES_API_KEY env.
//
// Docs:
//   Image 2.1 Flash: https://agnes-ai.com/zh-Hans/docs/agnes-image-21-flash
//   Video V2.0:      https://agnes-ai.com/zh-Hans/docs/agnes-video-v20
const fs = require('fs');
const path = require('path');
const os = require('os');

const IMAGE_BASE = process.env.AGNES_BASE_URL || 'https://apihub.agnes-ai.com/v1';
const VIDEO_STATUS_BASE = process.env.AGNES_VIDEO_STATUS_BASE || 'https://apihub.agnes-ai.com';

function readAgnesApiKey() {
  const home = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
  try {
    const text = fs.readFileSync(path.join(home, '.env'), 'utf-8');
    const m = text.match(/^AGNES_API_KEY=(.+)$/m);
    if (m) return m[1].trim();
  } catch (_) {}
  return process.env.AGNES_API_KEY || '';
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Convert file:// / abcyesno-local:// / raw Windows paths to a normalised
// local filesystem path that fs.* can consume.
function toLocalPath(src) {
  if (!src || typeof src !== 'string') return null;
  let fp = src;
  if (/^file:\/\//i.test(fp)) {
    fp = fp.replace(/^file:\/\//i, '');
    if (/^\/[A-Za-z]:/.test(fp)) fp = fp.slice(1);
    return fp;
  }
  if (/^abcyesno-local:\/\//i.test(fp)) {
    fp = decodeURIComponent(fp.replace(/^abcyesno-local:\/\//i, ''));
    if (/^\/[A-Za-z]:/.test(fp)) fp = fp.slice(1);
    return fp;
  }
  if (/^(https?:|data:)/i.test(fp)) return null;
  return fp;
}

function fileToDataUri(src) {
  const fp = toLocalPath(src);
  if (!fp) throw new Error(`无法把 ${src.slice(0, 120)} 转为本地路径`);
  if (!fs.existsSync(fp)) throw new Error(`文件不存在: ${fp}`);
  const buf = fs.readFileSync(fp);
  const ext = path.extname(fp).toLowerCase();
  const mime = ext === '.png' ? 'image/png' : ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : 'image/png';
  return `data:${mime};base64,${buf.toString('base64')}`;
}

// ── Image 2.1 Flash ──────────────────────────────────────────────────────
// POST {IMAGE_BASE}/images/generations
//   body: { model, prompt, size, ratio, extra_body:{ response_format:"url" } }
//   resp: { data:[{ url }] }
async function generateImage({ prompt, size = '2K', ratio = '1:1' }, key) {
  const apiKey = key || readAgnesApiKey();
  if (!apiKey) throw new Error('AGNES_API_KEY 未配置');
  if (!prompt) throw new Error('prompt 必填');
  const res = await fetch(`${IMAGE_BASE}/images/generations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: 'agnes-image-2.1-flash',
      prompt,
      size,
      ratio,
      extra_body: { response_format: 'url' },
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`IMAGE HTTP ${res.status}: ${JSON.stringify(data).slice(0, 300)}`);
  const url = data && data.data && data.data[0] && data.data[0].url;
  if (!url) throw new Error('IMAGE 响应缺少 url: ' + JSON.stringify(data).slice(0, 300));
  return url;
}

// ── Video V2.0 (async) ───────────────────────────────────────────────────
// POST {IMAGE_BASE}/videos
//   body: { model, prompt, image?, width, height, num_frames(8n+1), frame_rate }
//   resp: { video_id, status:"queued", seconds, size }
// Poll GET {VIDEO_STATUS_BASE}/agnesapi?video_id=<ID> until status:"completed"
//   resp: { status, metadata:{ url } }
async function generateVideo(
  { prompt, image, keyframes, width = 1152, height = 768, num_frames = 81, frame_rate = 24 },
  key
) {
  const apiKey = key || readAgnesApiKey();
  if (!apiKey) throw new Error('AGNES_API_KEY 未配置');
  if (!prompt) throw new Error('prompt 必填');
  // Agnes video API only accepts http(s) URLs or base64 image data. Convert
  // local workspace paths / file:// / abcyesno-local:// before sending.
  let resolvedImage = image;
  if (resolvedImage && !/^https?:/i.test(resolvedImage) && !/^data:/i.test(resolvedImage)) {
    resolvedImage = fileToDataUri(resolvedImage);
  }
  // Keyframes (multi-frame) mode: mirror the Python create_video contract --
  // when both first+last frames are supplied, send them as extra_body image
  // list with mode:"keyframes" (the top-level `image` is then ignored).
  let resolvedKeyframes = null;
  if (Array.isArray(keyframes) && keyframes.length) {
    resolvedKeyframes = keyframes.map((p) =>
      /^https?:/i.test(p) || /^data:/i.test(p) ? p : fileToDataUri(p)
    );
  }
  const body = {
    model: 'agnes-video-v2.0',
    prompt,
    width,
    height,
    num_frames,
    frame_rate,
  };
  if (resolvedKeyframes) {
    body.extra_body = { image: resolvedKeyframes, mode: 'keyframes' };
  } else if (resolvedImage) {
    body.image = resolvedImage;
  }
  const res = await fetch(`${IMAGE_BASE}/videos`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`VIDEO HTTP ${res.status}: ${JSON.stringify(data).slice(0, 300)}`);
  const videoId = data && data.video_id;
  if (!videoId) throw new Error('VIDEO 响应缺少 video_id: ' + JSON.stringify(data).slice(0, 300));

  const statusUrl = `${VIDEO_STATUS_BASE}/agnesapi?video_id=${encodeURIComponent(videoId)}`;
  const deadline = Date.now() + 1000 * 60 * 10; // 10 min safety cap
  while (Date.now() < deadline) {
    await sleep(4000);
    const st = await fetch(statusUrl, { headers: { Authorization: `Bearer ${apiKey}` } });
    const sd = await st.json().catch(() => ({}));
    const status = sd && sd.status;
    if (status === 'completed') {
      // Agnes returns the final URL at the TOP LEVEL (`url`), not nested under
      // metadata. Fall back to a few common shapes to be safe.
      const url =
        (sd && sd.url) ||
        (sd && sd.metadata && sd.metadata.url) ||
        (sd && sd.output) ||
        (sd && sd.download_url) ||
        null;
      if (!url) throw new Error('VIDEO completed 但缺少 url: ' + JSON.stringify(sd).slice(0, 300));
      return url;
    }
    if (status === 'failed' || status === 'error') {
      throw new Error('VIDEO 生成失败: ' + JSON.stringify(sd).slice(0, 300));
    }
  }
  throw new Error('VIDEO 轮询超时 (10min)');
}

// ── Media download (for exporting real assets into a local Jianying draft) ──
async function downloadMedia(url, destDir, name) {
  fs.mkdirSync(destDir, { recursive: true });
  const m = url.match(/\.(mp4|png|jpe?g|webp|gif|mp3|wav|m4a)(?:\?|$)/i);
  const ext = (m && m[1]) || 'bin';
  const dest = path.join(destDir, `${name}.${ext}`);

  // Local workspace files are passed through as paths; Node fetch cannot read
  // file:// or abcyesno-local://, so copy directly.
  const localPath = toLocalPath(url);

  if (localPath && fs.existsSync(localPath) && fs.statSync(localPath).isFile()) {
    fs.copyFileSync(localPath, dest);
    return dest;
  }

  const res = await fetch(url);
  if (!res.ok) throw new Error(`DOWNLOAD HTTP ${res.status} for ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(dest, buf);
  return dest;
}

module.exports = { readAgnesApiKey, generateImage, generateVideo, downloadMedia };
