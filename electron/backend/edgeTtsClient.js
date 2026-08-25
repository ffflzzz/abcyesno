// edgeTtsClient — self-contained Microsoft Edge TTS client (Node).
//
// Why not the `edge-tts` npm package? Its published build skips the
// Sec-MS-GEC auth handshake Microsoft now requires, so every request 403s.
// This module replicates the canonical python `edge-tts` protocol exactly:
//   1. GET a short-lived GEC token from https://edge.microsoft.com/translate/auth
//   2. open the speech WebSocket with Sec-MS-GEC / Sec-MS-GEC-Version as URL
//      query params (NOT headers — that's the part the npm package got wrong)
//   3. send SSML, stream back mp3 audio bytes
//
// Requires only `ws` (already in node_modules). No API key.

const { WebSocket } = require("ws");
const { randomUUID } = require("crypto");

const TRUSTED_CLIENT_TOKEN = "6A5AA1D4F64DD9C7E0672B5D2A01304C";
const WS_HOST = "speech.platform.bing.com";
const AUTH_URL = "https://edge.microsoft.com/translate/auth";

const WS_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.66 Safari/537.36 Edg/103.0.1264.44",
  Origin: "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
  "Accept-Encoding": "gzip, deflate, br",
  "Accept-Language": "en-US,en;q=0.9",
};

function prevDayGmt() {
  // python edge-tts uses the *previous* day's GMT date as the GEC version.
  return new Date(Date.now() - 86400000).toUTCString();
}

async function getGecToken() {
  try {
    const r = await fetch(AUTH_URL, { headers: { "User-Agent": "Mozilla/5.0" } });
    if (!r.ok) return "";
    const t = (await r.text()).trim();
    return t;
  } catch {
    return "";
  }
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

/**
 * synth — generate mp3 for `text`.
 * @returns {Promise<Buffer>} mp3 bytes
 */
function synth(text, voice = "zh-CN-XiaoxiaoNeural", ratePct = "+0%") {
  return new Promise(async (resolve, reject) => {
    let gecToken = "";
    try {
      gecToken = await getGecToken();
    } catch {
      /* ignore — token may be empty; server will reject with a clear error */
    }
    const url =
      `wss://${WS_HOST}/consumer/speech/synthesize/readaloud/edge/v1` +
      `?TrustedClientToken=${TRUSTED_CLIENT_TOKEN}` +
      `&Sec-MS-GEC=${encodeURIComponent(gecToken)}` +
      `&Sec-MS-GEC-Version=${encodeURIComponent(prevDayGmt())}`;

    const ws = new WebSocket(url, { headers: WS_HEADERS });
    const chunks = [];
    const timer = setTimeout(() => {
      try { ws.close(); } catch {}
      reject(new Error("edge-tts timed out"));
    }, 60000);

    ws.on("open", () => {
      const ssml = buildSsml(text, voice, ratePct);
      const reqId = randomUUID();
      ws.send(
        `X-RequestId:${reqId}\r\n` +
          `Content-Type:application/ssml+xml\r\n` +
          `X-Timestamp:${new Date().toISOString()}Z\r\n` +
          `Path:ssml\r\n\r\n${ssml}`
      );
    });

    ws.on("message", (data) => {
      const str = data.toString();
      if (str.includes("Path:turn.end")) {
        clearTimeout(timer);
        try { ws.close(); } catch {}
        resolve(Buffer.concat(chunks));
        return;
      }
      if (str.includes("Path:audio")) {
        const idx = str.indexOf("Path:audio");
        const headerEnd = str.indexOf("\r\n\r\n", idx);
        if (headerEnd !== -1) {
          const bin = data.slice(headerEnd + 4);
          if (bin.length) chunks.push(bin);
        }
      }
    });

    ws.on("error", (e) => {
      clearTimeout(timer);
      reject(e);
    });
  });
}

module.exports = { synth, getGecToken };
