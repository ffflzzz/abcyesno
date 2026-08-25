// edgeTtsClient — self-contained Microsoft Edge TTS client (Node).
//
// No API key. Replicates the canonical python `edge-tts` protocol.
//
// NOTE: Microsoft dropped the old `Sec-MS-GEC` token *fetch* endpoint
// (https://edge.microsoft.com/translate/auth now 404s). The current
// edge-tts computes the GEC token LOCALLY as:
//     ticks = (unixNow + WIN_EPOCH)            // 1601 epoch, seconds
//     ticks = floor(ticks / 300) * 300         // round down to 5 min
//     filetime = ticks * 1e7                    // 100-ns intervals
//     token = SHA256(`${filetime}${TRUSTED_CLIENT_TOKEN}`).hex.upper()
// and pins `Sec-MS-GEC-Version` to a fixed Chromium build string.
//
// Requires only `ws` (already in node_modules).

const { WebSocket } = require("ws");
const crypto = require("crypto");

// ---- Current protocol constants (mirror edge_tts/constants.py) ----
const TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4";
const WS_HOST = "speech.platform.bing.com";
const CHROMIUM_FULL_VERSION = "143.0.3650.75";
const SEC_MS_GEC_VERSION = `1-${CHROMIUM_FULL_VERSION}`;

// Windows filetime epoch offset (seconds between 1601-01-01 and 1970-01-01).
const WIN_EPOCH = 11644473600n;
const ROUND_SECONDS = 300n; // round down to nearest 5 minutes
const HUNDRED_NS = 10000000n; // 1 second = 10_000_000 100-ns ticks

const WS_HEADERS = {
  Pragma: "no-cache",
  "Cache-Control": "no-cache",
  Origin: "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
  "Sec-WebSocket-Version": "13",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    `(KHTML, like Gecko) Chrome/${CHROMIUM_FULL_VERSION.split(".")[0]}.0.0.0 ` +
    `Safari/537.36 Edg/${CHROMIUM_FULL_VERSION.split(".")[0]}.0.0.0`,
  "Accept-Encoding": "gzip, deflate, br, zstd",
  "Accept-Language": "en-US,en;q=0.9",
};

// ---- GEC token (computed locally, no network) ----
function generateSecMsGec() {
  const nowSec = BigInt(Math.floor(Date.now() / 1000));
  let ticks = nowSec + WIN_EPOCH;
  ticks -= ticks % ROUND_SECONDS; // round down to 5-minute boundary
  const filetime = ticks * HUNDRED_NS; // Windows filetime (100-ns units)
  const str = filetime.toString() + TRUSTED_CLIENT_TOKEN;
  return crypto.createHash("sha256").update(str, "ascii").digest("hex").toUpperCase();
}

// MUID cookie, as edge_tts/DRM.headers_with_muid appends.
function generateMuid() {
  return crypto.randomBytes(16).toString("hex").toUpperCase();
}

// Javascript-style date string used in X-Timestamp (mirrors edge_tts date_to_string).
function dateToString() {
  // %a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${days[d.getUTCDay()]} ${months[d.getUTCMonth()]} ${p(d.getUTCDate())} ` +
    `${d.getUTCFullYear()} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} ` +
    "GMT+0000 (Coordinated Universal Time)"
  );
}

function buildSsml(text, voice, ratePct) {
  const escaped = String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return (
    `<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' ` +
    `xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='zh-CN'>` +
    `<voice name='${voice}'><prosody rate='${ratePct}'>${escaped}</prosody></voice></speak>`
  );
}

function connectId() {
  // UUID without dashes (mirrors edge_tts connect_id).
  return crypto.randomUUID().replace(/-/g, "");
}

/**
 * synth — generate mp3 for `text`.
 * @returns {Promise<Buffer>} mp3 bytes
 */
function synth(text, voice = "zh-CN-XiaoxiaoNeural", ratePct = "+0%") {
  return new Promise((resolve, reject) => {
    const gec = generateSecMsGec();
    const url =
      `wss://${WS_HOST}/consumer/speech/synthesize/readaloud/edge/v1` +
      `?TrustedClientToken=${TRUSTED_CLIENT_TOKEN}` +
      `&ConnectionId=${connectId()}` +
      `&Sec-MS-GEC=${encodeURIComponent(gec)}` +
      `&Sec-MS-GEC-Version=${encodeURIComponent(SEC_MS_GEC_VERSION)}`;

    const headers = { ...WS_HEADERS, Cookie: `muid=${generateMuid()};` };
    const ws = new WebSocket(url, { headers });
    const chunks = [];
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { ws.close(); } catch {}
      reject(new Error("edge-tts timed out"));
    }, 60000);

    ws.on("open", () => {
      // 1) speech.config (mirrors edge_tts send_command_request)
      ws.send(
        `X-Timestamp:${dateToString()}\r\n` +
          `Content-Type:application/json; charset=utf-8\r\n` +
          `Path:speech.config\r\n\r\n` +
          `{"context":{"synthesis":{"audio":{"metadataoptions":{` +
          `"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"` +
          `},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n`
      );
      // 2) SSML
      const ssml = buildSsml(text, voice, ratePct);
      const reqId = connectId();
      ws.send(
        `X-RequestId:${reqId}\r\n` +
          `Content-Type:application/ssml+xml\r\n` +
          `X-Timestamp:${dateToString()}Z\r\n` +
          `Path:ssml\r\n\r\n${ssml}`
      );
    });

    ws.on("message", (data) => {
      // edge-tts binary frames: [2-byte BE header length][ASCII header][audio bytes].
      // Every audio/config/turn-end frame carries this prefix.
      let header = "";
      let body = data;
      if (data.length >= 2) {
        const headerLen = data.readUInt16BE(0);
        if (headerLen > 0 && headerLen < data.length) {
          header = data.slice(2, 2 + headerLen).toString("utf8");
          body = data.slice(2 + headerLen);
        }
      }
      // Fallback for non-prefixed frames (legacy/text): search inline.
      if (!header.includes("Path:")) {
        header = data.toString("utf8");
      }

      if (header.includes("Path:turn.end")) {
        clearTimeout(timer);
        if (settled) return;
        settled = true;
        try { ws.close(); } catch {}
        resolve(Buffer.concat(chunks));
        return;
      }
      if (header.includes("Path:audio")) {
        if (body.length) chunks.push(body);
      }
    });

    ws.on("error", (e) => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      reject(e);
    });
  });
}

// Kept for backwards compatibility / testing; token is now local, not fetched.
function getGecToken() {
  return generateSecMsGec();
}

module.exports = { synth, getGecToken, generateSecMsGec };
