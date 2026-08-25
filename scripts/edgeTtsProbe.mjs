// Probe: replicate edge-tts protocol WITH the Sec-MS-GEC auth token, to verify
// the 403 can be fixed without the (broken) edge-tts npm package.
import { WebSocket } from "ws";
import { randomUUID } from "node:crypto";

const TOKEN = "6A5AA1D4F64DD9C7E0672B5D2A01304C";
const WS_URL = `wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=${TOKEN}`;

async function getGec() {
  const r = await fetch("https://edge.microsoft.com/translate/auth", {
    headers: { "User-Agent": "Mozilla/5.0" },
  });
  const text = (await r.text()).trim();
  const prev = new Date(Date.now() - 86400000).toUTCString();
  return { token: text, version: prev };
}

function buildSsml(text, voice, ratePct) {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return `<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='zh-CN'><voice name='${voice}'><prosody rate='${ratePct}'>${escaped}</prosody></voice></speak>`;
}

async function synth(text, voice = "zh-CN-XiaoxiaoNeural", ratePct = "+0%") {
  const gec = await getGec();
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(
      `${WS_URL}&ConnectionId=${randomUUID()}`,
      {
        host: "speech.platform.bing.com",
        origin: "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.66 Safari/537.36 Edg/103.0.1264.44",
          "Sec-MS-GEC": gec.token,
          "Sec-MS-GEC-Version": gec.version,
        },
      }
    );
    const chunks = [];
    let audioLen = 0;
    const timer = setTimeout(() => {
      ws.close();
      reject(new Error("timeout"));
    }, 45000);
    ws.on("open", () => {
      const ssml = buildSsml(text, voice, ratePct);
      ws.send(
        `X-RequestId:${randomUUID()}\r\nContent-Type:application/ssml+xml\r\n` +
          `X-Timestamp:${new Date().toISOString()}Z\r\nPath:ssml\r\n\r\n${ssml}`
      );
    });
    ws.on("message", (data) => {
      const str = data.toString();
      if (str.includes("Path:audio")) {
        const idx = str.indexOf("Path:audio");
        const headerEnd = str.indexOf("\r\n\r\n", idx);
        if (headerEnd !== -1) {
          const bin = data.slice(headerEnd + 4);
          if (bin.length) {
            chunks.push(bin);
            audioLen += bin.length;
          }
        }
      } else if (str.includes("Path:turn.end")) {
        clearTimeout(timer);
        ws.close();
        resolve(Buffer.concat(chunks));
      }
    });
    ws.on("error", (e) => {
      clearTimeout(timer);
      reject(e);
    });
  });
}

synth("你好，这是朗读测试。", "zh-CN-XiaoxiaoNeural", "+0%")
  .then((buf) => {
    console.log("OK mp3 bytes:", buf.length, "head:", buf.slice(0, 4).toString("hex"));
    process.exit(0);
  })
  .catch((e) => {
    console.error("ERR:", e.message);
    process.exit(1);
  });
