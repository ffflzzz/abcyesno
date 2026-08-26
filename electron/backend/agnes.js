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

// Public/default key (sk- prefix) used when the Token Plan primary key's
// daily video-second quota or RPM cap is exhausted. Read from the env (injected
// by hermes-runner) or HERMES_HOME/.env. Returns '' when not configured.
function readAgnesFallbackKey() {
  const home = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
  try {
    const text = fs.readFileSync(path.join(home, '.env'), 'utf-8');
    const m = text.match(/^AGNES_FALLBACK_API_KEY=(.+)$/m);
    if (m) return m[1].trim();
  } catch (_) {}
  return process.env.AGNES_FALLBACK_API_KEY || '';
}

// Detect Agnes quota / rate-limit responses worth a key fallback. The video
// create endpoint returns 429 when the Token Plan daily-second quota or RPM
// cap is hit. We also match other 4xx whose body mentions quota/limit/plan/
// usage as a belt-and-suspenders signal.
function _isQuotaError(e) {
  const msg = (e && e.message) || '';
  if (/HTTP 429/i.test(msg)) return true;
  if (/\b(quota|exceed|limit|plan|subscription|too many|usage limit|daily)\b/i.test(msg)) return true;
  return false;
}

// The public/fallback key is rate-limited to 1 RPM. Serialize every fallback
// call so we never burst past it. A module-level timestamp + sleep is enough
// because agui-server runs in a single Node process.
let _lastFallbackTs = 0;
async function _throttleFallback() {
  const minGap = 61 * 1000;
  const now = Date.now();
  const wait = minGap - (now - _lastFallbackTs);
  if (wait > 0) await sleep(wait);
  _lastFallbackTs = Date.now();
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

// agnes-video-2.5-flash (https://www.agnes-ai.com/zh-Hans/docs/agnes-video-25-flash)
const VIDEO_MODEL = 'agnes-video-2.5-flash';

function aspectRatioForDims(width, height) {
  const ratios = [
    ['21:9', 21 / 9],
    ['16:9', 16 / 9],
    ['4:3', 4 / 3],
    ['1:1', 1],
    ['3:4', 3 / 4],
    ['9:16', 9 / 16],
  ];
  if (!width || !height) return '16:9';
  const r = width / height;
  let best = ratios[0];
  for (const [label, val] of ratios) {
    if (Math.abs(val - r) < Math.abs(best[1] - r)) best = [label, val];
  }
  return best[0];
}

function secondsForDuration(numFrames, frameRate) {
  const fps = Math.max(1, frameRate || 24);
  const s = Math.max(4, Math.min(12, Math.round((numFrames || 0) / fps)));
  return String(s);
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
//
// Token-Plan -> public-key fallback: if the primary (Token Plan) key returns
// 429 / quota-exceeded, transparently retry with AGNES_FALLBACK_API_KEY (the
// public key, unlimited seconds but 1 RPM). See generateVideo() below.
async function _generateVideoWithKey(
  { prompt, image, keyframes, reference_images, width = 1152, height = 768, num_frames = 81, frame_rate = 24, seconds, aspect_ratio },
  apiKey
) {
  if (!apiKey) throw new Error('AGNES_API_KEY 未配置');
  if (!prompt) throw new Error('prompt 必填');
  // Agnes video API only accepts http(s) URLs or base64 image data. Convert
  // local workspace paths / file:// / abcyesno-local:// before sending.
  const frameRef = (p) => (/^https?:/i.test(p) || /^data:/i.test(p) ? p : fileToDataUri(p));
  let resolvedImage = image;
  if (resolvedImage && !/^https?:/i.test(resolvedImage) && !/^data:/i.test(resolvedImage)) {
    resolvedImage = fileToDataUri(resolvedImage);
  }
  // Keyframes: accept either a [first, last] array (Python/agent contract) or a
  // single URL string (legacy frontend single-shot pass-through).
  let resolvedKeyframes = null;
  if (Array.isArray(keyframes) && keyframes.length) {
    resolvedKeyframes = keyframes.map(frameRef);
  } else if (typeof keyframes === 'string' && keyframes) {
    resolvedKeyframes = [frameRef(keyframes)];
  }

  // agnes-video-2.5-flash drops width/height/num_frames/frame_rate in favor of
  // mode + seconds + size("720P") + aspect_ratio. Accept explicit seconds/
  // aspect_ratio when the caller supplies them, else map from the legacy knobs.
  const body = {
    model: VIDEO_MODEL,
    prompt,
    seconds: seconds || secondsForDuration(num_frames, frame_rate),
    size: '720P',
    aspect_ratio: aspect_ratio || aspectRatioForDims(width, height),
  };
  if (resolvedKeyframes && resolvedKeyframes.length >= 2) {
    // Endpoint-pinned transition: first + last frames (native flash support).
    body.mode = 'keyframe';
    body.first_frame = resolvedKeyframes[0];
    body.last_frame = resolvedKeyframes[1];
  } else if (resolvedKeyframes && resolvedKeyframes.length === 1) {
    body.mode = 'keyframe';
    body.first_frame = resolvedKeyframes[0];
  } else if (resolvedImage) {
    // Single first-frame anchor (img2vid equivalent).
    body.mode = 'keyframe';
    body.first_frame = resolvedImage;
  } else {
    body.mode = 'text';
  }
  // Character/identity reference: flash supports mode="reference" + images
  // (<=5), but reference mode is mutually exclusive with first/last-frame
  // anchoring — the per-shot first frame IS the identity anchor. No-op.
  // (reference_images intentionally unused on the keyframe/text paths.)

  const res = await fetch(`${IMAGE_BASE}/videos`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`VIDEO HTTP ${res.status}: ${JSON.stringify(data).slice(0, 300)}`);
  const videoId = data && data.video_id;
  if (!videoId) throw new Error('VIDEO 响应缺少 video_id: ' + JSON.stringify(data).slice(0, 300));

  // flash requires model_name on the poll for keyframe/reference tasks.
  const statusUrl = `${VIDEO_STATUS_BASE}/agnesapi?video_id=${encodeURIComponent(videoId)}&model_name=${VIDEO_MODEL}`;
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

async function generateVideo(opts, key) {
  const primaryKey = key || readAgnesApiKey();
  if (!primaryKey) throw new Error('AGNES_API_KEY 未配置');
  const fbKey = readAgnesFallbackKey();
  // Build the key chain: primary first, then the public fallback key (if a
  // distinct one is configured). We only ever advance to the fallback on a
  // 429 / quota-exceeded error from the primary.
  const chain = [primaryKey];
  if (fbKey && fbKey !== primaryKey) chain.push(fbKey);

  let lastErr;
  for (let i = 0; i < chain.length; i++) {
    const apiKey = chain[i];
    const isFallback = i > 0;
    try {
      if (isFallback) {
        console.log('[agnes] Token Plan 主 key 额度/限流，回退到 Agnes 公网 key 重试');
        await _throttleFallback();
      }
      return await _generateVideoWithKey(opts, apiKey);
    } catch (e) {
      lastErr = e;
      // Surface immediately if we're already on the fallback key, if this
      // isn't a quota/limit error, or if there's no fallback key at all.
      if (isFallback || !_isQuotaError(e) || chain.length === 1) {
        throw e;
      }
      // Primary key hit quota/limit -> loop retries on the fallback key.
    }
  }
  throw lastErr || new Error('video generation failed');
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
