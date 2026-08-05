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
  { prompt, image, width = 1152, height = 768, num_frames = 81, frame_rate = 24 },
  key
) {
  const apiKey = key || readAgnesApiKey();
  if (!apiKey) throw new Error('AGNES_API_KEY 未配置');
  if (!prompt) throw new Error('prompt 必填');
  const res = await fetch(`${IMAGE_BASE}/videos`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: 'agnes-video-v2.0',
      prompt,
      width,
      height,
      num_frames,
      frame_rate,
      ...(image ? { image } : {}),
    }),
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
  const res = await fetch(url);
  if (!res.ok) throw new Error(`DOWNLOAD HTTP ${res.status} for ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const m = url.match(/\.(mp4|png|jpe?g|webp|gif)(?:\?|$)/i);
  const ext = (m && m[1]) || 'bin';
  const dest = path.join(destDir, `${name}.${ext}`);
  fs.writeFileSync(dest, buf);
  return dest;
}

module.exports = { readAgnesApiKey, generateImage, generateVideo, downloadMedia };
