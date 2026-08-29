var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __esm = (fn, res) => function __init() {
  return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
};
var __commonJS = (cb, mod) => function __require() {
  return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
};
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// electron/backend/wechat_bridge/src/constants.ts
function resolveDataDir() {
  if (process.env.WCC_DATA_DIR) return process.env.WCC_DATA_DIR;
  const hermesHome = process.env.HERMES_HOME;
  if (hermesHome) return (0, import_node_path.join)(hermesHome, "wechat_bridge");
  return (0, import_node_path.join)((0, import_node_os.homedir)(), ".wechat-claude-code");
}
var import_node_os, import_node_path, DATA_DIR, DEFAULT_WORKING_DIR, CDN_BASE_URL;
var init_constants = __esm({
  "electron/backend/wechat_bridge/src/constants.ts"() {
    import_node_os = require("node:os");
    import_node_path = require("node:path");
    DATA_DIR = resolveDataDir();
    DEFAULT_WORKING_DIR = (0, import_node_path.join)((0, import_node_os.homedir)(), "Documents", "ClaudeCode");
    CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c";
  }
});

// electron/backend/wechat_bridge/src/logger.ts
function cleanupOldLogs() {
  try {
    const files = (0, import_node_fs.readdirSync)(LOG_DIR).filter((f) => f.startsWith("bridge-") && f.endsWith(".log")).sort();
    while (files.length > MAX_LOG_FILES) {
      (0, import_node_fs.unlinkSync)((0, import_node_path2.join)(LOG_DIR, files.shift()));
    }
  } catch {
  }
}
function redact(obj) {
  const raw = typeof obj === "string" ? obj : JSON.stringify(obj);
  if (!raw) return raw;
  let safe = raw;
  safe = safe.replace(/Bearer\s+[^\s"\\]+/gi, "Bearer ***");
  safe = safe.replace(
    /"(?:(?:[\w]+_)?[Tt]oken|(?:[\w]+_)?[Ss]ecret|(?:[\w]+_)?[Pp]assword|(?:[\w]+_)?api_key|[Aa]es_[Kk]ey)"\s*:\s*"[^"]*"/gi,
    (match) => {
      const key = match.match(/"[^"]*"/)?.[0] ?? '""';
      return `${key}: "***"`;
    }
  );
  return safe;
}
function ensureLogDir() {
  (0, import_node_fs.mkdirSync)(LOG_DIR, { recursive: true });
  cleanupOldLogs();
}
function getLogFilePath() {
  const now = new Date(Date.now() + 8 * 60 * 60 * 1e3);
  const date = now.toISOString().slice(0, 10);
  return (0, import_node_path2.join)(LOG_DIR, `bridge-${date}.log`);
}
function writeLogLine(level, message, data) {
  ensureLogDir();
  const ts = new Date(Date.now() + 8 * 60 * 60 * 1e3).toISOString();
  const timestamp = ts.replace("Z", "+08:00");
  const parts = [timestamp, level, message];
  if (data !== void 0) {
    parts.push(redact(data));
  }
  const line = parts.join(" ") + "\n";
  (0, import_node_fs.appendFileSync)(getLogFilePath(), line, "utf-8");
}
var import_node_fs, import_node_path2, LOG_DIR, MAX_LOG_FILES, logger;
var init_logger = __esm({
  "electron/backend/wechat_bridge/src/logger.ts"() {
    import_node_fs = require("node:fs");
    import_node_path2 = require("node:path");
    init_constants();
    LOG_DIR = (0, import_node_path2.join)(DATA_DIR, "logs");
    MAX_LOG_FILES = 30;
    logger = {
      info(message, data) {
        writeLogLine("INFO", message, data);
      },
      warn(message, data) {
        writeLogLine("WARN", message, data);
      },
      error(message, data) {
        writeLogLine("ERROR", message, data);
      },
      debug(message, data) {
        writeLogLine("DEBUG", message, data);
      }
    };
  }
});

// electron/backend/wechat_bridge/src/store.ts
function validateAccountId(accountId) {
  if (!/^[a-zA-Z0-9_.@=-]+$/.test(accountId)) {
    throw new Error(`Invalid accountId: "${accountId}"`);
  }
}
function loadJson(filePath, fallback) {
  try {
    const raw = (0, import_node_fs2.readFileSync)(filePath, "utf-8");
    return JSON.parse(raw);
  } catch (err) {
    const code = err.code;
    if (code !== "ENOENT") {
      logger.warn("loadJson failed, using fallback", { filePath, error: err instanceof Error ? err.message : String(err) });
    }
    return fallback;
  }
}
function saveJson(filePath, data) {
  (0, import_node_fs2.mkdirSync)((0, import_node_path3.dirname)(filePath), { recursive: true });
  const raw = JSON.stringify(data, null, 2) + "\n";
  (0, import_node_fs2.writeFileSync)(filePath, raw, "utf-8");
  if (process.platform !== "win32") {
    (0, import_node_fs2.chmodSync)(filePath, 384);
  }
}
var import_node_fs2, import_node_path3;
var init_store = __esm({
  "electron/backend/wechat_bridge/src/store.ts"() {
    import_node_fs2 = require("node:fs");
    import_node_path3 = require("node:path");
    init_logger();
  }
});

// electron/backend/wechat_bridge/src/wechat/accounts.ts
function accountPath(accountId) {
  validateAccountId(accountId);
  return (0, import_node_path4.join)(ACCOUNTS_DIR, `${accountId}.json`);
}
function saveAccount(data) {
  const filePath = accountPath(data.accountId);
  saveJson(filePath, data);
  logger.info("Account saved", { accountId: data.accountId });
}
function loadAccount(accountId) {
  const filePath = accountPath(accountId);
  const data = loadJson(filePath, null);
  if (data) {
    logger.info("Account loaded", { accountId });
  }
  return data;
}
function loadLatestAccount() {
  try {
    const files = (0, import_node_fs3.readdirSync)(ACCOUNTS_DIR).filter((f) => f.endsWith(".json"));
    if (files.length === 0) return null;
    let latestFile = files[0];
    let latestMtime = 0;
    for (const file of files) {
      const stat = (0, import_node_fs3.statSync)((0, import_node_path4.join)(ACCOUNTS_DIR, file));
      if (stat.mtimeMs > latestMtime) {
        latestMtime = stat.mtimeMs;
        latestFile = file;
      }
    }
    const accountId = latestFile.replace(/\.json$/, "");
    return loadAccount(accountId);
  } catch {
    return null;
  }
}
var import_node_path4, import_node_fs3, DEFAULT_BASE_URL, ACCOUNTS_DIR;
var init_accounts = __esm({
  "electron/backend/wechat_bridge/src/wechat/accounts.ts"() {
    import_node_path4 = require("node:path");
    import_node_fs3 = require("node:fs");
    init_store();
    init_logger();
    init_constants();
    DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com";
    ACCOUNTS_DIR = (0, import_node_path4.join)(DATA_DIR, "accounts");
  }
});

// electron/backend/wechat_bridge/src/wechat/login.ts
function sleep(ms) {
  return new Promise((resolve3) => setTimeout(resolve3, ms));
}
async function startQrLogin() {
  logger.info("Requesting QR code");
  const res = await fetch(QR_CODE_URL);
  if (!res.ok) {
    throw new Error(`Failed to get QR code: HTTP ${res.status}`);
  }
  const data = await res.json();
  if (data.ret !== 0 || !data.qrcode_img_content || !data.qrcode) {
    throw new Error(`Failed to get QR code (ret=${data.ret})`);
  }
  logger.info("QR code obtained", { qrcodeId: data.qrcode });
  return {
    qrcodeUrl: data.qrcode_img_content,
    qrcodeId: data.qrcode
  };
}
async function waitForQrScan(qrcodeId) {
  let currentQrcodeId = qrcodeId;
  while (true) {
    const url = `${QR_STATUS_URL}?qrcode=${encodeURIComponent(currentQrcodeId)}`;
    logger.debug("Polling QR status", { qrcodeId: currentQrcodeId });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6e4);
    let res;
    try {
      res = await fetch(url, { signal: controller.signal });
    } catch (e) {
      clearTimeout(timer);
      if (e.name === "AbortError" || e.code === "ETIMEDOUT") {
        logger.info("QR poll timed out, retrying");
        continue;
      }
      throw e;
    }
    clearTimeout(timer);
    if (!res.ok) {
      throw new Error(`Failed to check QR status: HTTP ${res.status}`);
    }
    const data = await res.json();
    logger.debug("QR status response", { status: data.status });
    switch (data.status) {
      case "wait":
      case "scaned":
        break;
      case "confirmed": {
        if (!data.bot_token || !data.ilink_bot_id || !data.ilink_user_id) {
          throw new Error("QR confirmed but missing required fields in response");
        }
        const accountData = {
          botToken: data.bot_token,
          accountId: data.ilink_bot_id,
          baseUrl: data.baseurl || DEFAULT_BASE_URL,
          userId: data.ilink_user_id,
          createdAt: (/* @__PURE__ */ new Date()).toISOString()
        };
        saveAccount(accountData);
        logger.info("QR login successful", { accountId: accountData.accountId });
        return accountData;
      }
      case "expired": {
        logger.info("QR code expired");
        throw new Error("QR code expired");
      }
      default:
        logger.warn("Unknown QR status", { status: data.status, retmsg: data.retmsg });
        const status = data.status ?? "";
        if (status && (status.includes("not_support") || status.includes("version") || status.includes("forbid") || status.includes("reject") || status.includes("cancel"))) {
          throw new Error(`\u4E8C\u7EF4\u7801\u626B\u63CF\u5931\u8D25: ${data.retmsg || status}`);
        }
        if (data.retmsg) {
          throw new Error(`\u4E8C\u7EF4\u7801\u626B\u63CF\u5931\u8D25: ${data.retmsg}`);
        }
        break;
    }
    await sleep(POLL_INTERVAL_MS);
  }
}
var QR_CODE_URL, QR_STATUS_URL, POLL_INTERVAL_MS;
var init_login = __esm({
  "electron/backend/wechat_bridge/src/wechat/login.ts"() {
    init_accounts();
    init_logger();
    QR_CODE_URL = `${DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3`;
    QR_STATUS_URL = `${DEFAULT_BASE_URL}/ilink/bot/get_qrcode_status`;
    POLL_INTERVAL_MS = 3e3;
  }
});

// electron/backend/wechat_bridge/src/wechat/api.ts
function generateUin() {
  const buf = new Uint8Array(4);
  crypto.getRandomValues(buf);
  return Buffer.from(buf).toString("base64");
}
var WeChatApi;
var init_api = __esm({
  "electron/backend/wechat_bridge/src/wechat/api.ts"() {
    init_logger();
    WeChatApi = class _WeChatApi {
      token;
      baseUrl;
      uin;
      nextSendTime = /* @__PURE__ */ new Map();
      static MIN_SEND_INTERVAL = 2500;
      // Cooldown applied after a rate-limit (ret:-2). Aligned with the circuit
      // breaker window so they don't fight each other.
      static RATE_LIMIT_COOLDOWN_MS = 3e4;
      // ── Circuit breaker ────────────────────────────────────────────────────
      // Borrowed from Hermes WeChat adapter: trip after the first genuine
      // rate-limit in a 30s window, stay open 30s. While open, all sends fail
      // fast without hitting the API — breaking the 14-minute "head-banging"
      // loop we observed in production logs.
      static CIRCUIT_THRESHOLD = 1;
      static CIRCUIT_WINDOW_MS = 3e4;
      static CIRCUIT_OPEN_MS = 3e4;
      _rateLimitEvents = [];
      _circuitUntil = 0;
      // ret:-2 + errmsg="unknown error" is a stale-session signal (same family
      // as errcode:-14), not a real rate-limit. Pause that user 10 minutes
      // instead of cycling through the rate-limit path.
      static STALE_SESSION_PAUSE_MS = 10 * 60 * 1e3;
      constructor(token, baseUrl = "https://ilinkai.weixin.qq.com") {
        if (baseUrl) {
          try {
            const url = new URL(baseUrl);
            const allowedHosts = ["weixin.qq.com", "wechat.com"];
            const isAllowed = allowedHosts.some((h) => url.hostname === h || url.hostname.endsWith("." + h));
            if (url.protocol !== "https:" || !isAllowed) {
              logger.warn("Untrusted baseUrl, using default", { baseUrl });
              baseUrl = "https://ilinkai.weixin.qq.com";
            }
          } catch {
            logger.warn("Invalid baseUrl, using default", { baseUrl });
            baseUrl = "https://ilinkai.weixin.qq.com";
          }
        }
        this.token = token;
        this.baseUrl = baseUrl.replace(/\/+$/, "");
        this.uin = generateUin();
      }
      headers() {
        return {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${this.token}`,
          "AuthorizationType": "ilink_bot_token",
          "X-WECHAT-UIN": this.uin
        };
      }
      async request(path2, body, timeoutMs = 15e3) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const url = `${this.baseUrl}/${path2}`;
        logger.debug("API request", { url, body });
        try {
          const res = await fetch(url, {
            method: "POST",
            headers: this.headers(),
            body: JSON.stringify(body),
            signal: controller.signal
          });
          if (!res.ok) {
            const text = await res.text();
            throw new Error(`HTTP ${res.status}: ${text}`);
          }
          const json = await res.json();
          logger.debug("API response", json);
          return json;
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            throw new Error(`Request to ${url} timed out after ${timeoutMs}ms`);
          }
          throw err;
        } finally {
          clearTimeout(timer);
        }
      }
      /** Long-poll for new messages. Timeout 35s for long-polling. */
      async getUpdates(buf) {
        return this.request(
          "ilink/bot/getupdates",
          buf ? { get_updates_buf: buf } : {},
          35e3
        );
      }
      /** Send a message to a user. Per-user rate limited, retries on rate-limit (ret: -2). */
      async sendMessage(req) {
        if (this._isCircuitOpen()) {
          const remainingSec = Math.ceil((this._circuitUntil - Date.now()) / 1e3);
          logger.warn("sendMessage rejected by circuit breaker", { remainingSec });
          throw new Error(`circuit breaker open, ${remainingSec}s remaining`);
        }
        const userId = req.msg?.to_user_id;
        if (userId) {
          const now = Date.now();
          const nextAvailable = (this.nextSendTime.get(userId) ?? 0) + _WeChatApi.MIN_SEND_INTERVAL;
          const sendAt = Math.max(now, nextAvailable);
          this.nextSendTime.set(userId, sendAt);
          const waitMs = sendAt - now;
          if (waitMs > 0) {
            logger.debug("Rate limiter waiting", { userId, waitMs });
            await new Promise((r) => setTimeout(r, waitMs));
          }
        }
        const MAX_RETRIES = 2;
        let delay = 3e3;
        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
          if (this._isCircuitOpen()) {
            const remainingSec = Math.ceil((this._circuitUntil - Date.now()) / 1e3);
            logger.warn("sendMessage aborted mid-retry by circuit breaker", { attempt, remainingSec });
            throw new Error(`circuit breaker open during retry, ${remainingSec}s remaining`);
          }
          const res = await this.request("ilink/bot/sendmessage", req);
          if (res.ret === -2) {
            const errmsg = (res.errmsg ?? "").toLowerCase();
            if (errmsg === "unknown error") {
              logger.warn("sendMessage stale session detected (ret:-2 + unknown error)", { userId });
              if (userId) {
                this.nextSendTime.set(userId, Date.now() + _WeChatApi.STALE_SESSION_PAUSE_MS);
              }
              throw new Error("stale session \u2014 user must send a message to refresh context_token");
            }
            this._tripCircuit();
            if (userId) {
              this.nextSendTime.set(userId, Date.now() + _WeChatApi.RATE_LIMIT_COOLDOWN_MS);
            }
            if (attempt === MAX_RETRIES) {
              logger.warn("sendMessage rate-limited after max retries", { attempts: MAX_RETRIES });
              throw new Error(`sendMessage rate-limited after ${MAX_RETRIES} retries`);
            }
            logger.warn("sendMessage rate-limited (ret:-2), retrying", { attempt, delayMs: delay });
            await new Promise((r) => setTimeout(r, delay));
            delay = Math.min(delay * 2, 15e3);
            continue;
          }
          return;
        }
      }
      // ── Circuit breaker helpers ────────────────────────────────────────────
      /** True while the breaker is open (sends should fail fast). */
      _isCircuitOpen() {
        if (this._circuitUntil === 0) return false;
        if (Date.now() >= this._circuitUntil) {
          this._circuitUntil = 0;
          this._rateLimitEvents.length = 0;
          return false;
        }
        return true;
      }
      /** Record a rate-limit event and open the breaker if threshold is met. */
      _tripCircuit() {
        const now = Date.now();
        const windowStart = now - _WeChatApi.CIRCUIT_WINDOW_MS;
        while (this._rateLimitEvents.length > 0 && this._rateLimitEvents[0] < windowStart) {
          this._rateLimitEvents.shift();
        }
        this._rateLimitEvents.push(now);
        if (this._rateLimitEvents.length >= _WeChatApi.CIRCUIT_THRESHOLD) {
          const openUntil = Math.max(this._circuitUntil, now + _WeChatApi.CIRCUIT_OPEN_MS);
          if (openUntil > this._circuitUntil) {
            logger.warn("Circuit breaker tripped", {
              events: this._rateLimitEvents.length,
              openMs: _WeChatApi.CIRCUIT_OPEN_MS
            });
          }
          this._circuitUntil = openUntil;
        }
      }
      /** Fetch bot config (includes typing_ticket). */
      async getConfig(ilinkUserId, contextToken) {
        return this.request(
          "ilink/bot/getconfig",
          { ilink_user_id: ilinkUserId, context_token: contextToken },
          1e4
        );
      }
      /** Send a typing indicator to a user. */
      async sendTyping(req) {
        await this.request("ilink/bot/sendtyping", req, 1e4);
      }
      /** Get a presigned upload URL for media files. */
      async getUploadUrl(req) {
        return this.request("ilink/bot/getuploadurl", req);
      }
    };
  }
});

// electron/backend/wechat_bridge/src/wechat/sync-buf.ts
function loadSyncBuf() {
  return loadJson(SYNC_BUF_PATH, "");
}
function saveSyncBuf(buf) {
  saveJson(SYNC_BUF_PATH, buf);
}
var import_node_path5, SYNC_BUF_PATH;
var init_sync_buf = __esm({
  "electron/backend/wechat_bridge/src/wechat/sync-buf.ts"() {
    init_store();
    init_constants();
    import_node_path5 = require("node:path");
    SYNC_BUF_PATH = (0, import_node_path5.join)(DATA_DIR, "get_updates_buf");
  }
});

// electron/backend/wechat_bridge/src/wechat/monitor.ts
function createMonitor(api, callbacks) {
  const controller = new AbortController();
  let stopped = false;
  const recentMsgIds = /* @__PURE__ */ new Set();
  const MAX_MSG_IDS = 1e3;
  async function run() {
    let consecutiveFailures = 0;
    while (!controller.signal.aborted) {
      try {
        const buf = loadSyncBuf();
        logger.debug("Polling for messages", { hasBuf: buf.length > 0 });
        const resp = await api.getUpdates(buf || void 0);
        if (resp.ret === SESSION_EXPIRED_ERRCODE) {
          logger.warn("Session expired, pausing for 1 hour");
          callbacks.onSessionExpired();
          await sleep2(SESSION_EXPIRED_PAUSE_MS, controller.signal);
          consecutiveFailures = 0;
          continue;
        }
        if (resp.ret !== void 0 && resp.ret !== 0) {
          logger.warn("getUpdates returned error", { ret: resp.ret, retmsg: resp.retmsg });
        }
        if (resp.get_updates_buf) {
          saveSyncBuf(resp.get_updates_buf);
        }
        const messages = resp.msgs ?? [];
        if (messages.length > 0) {
          logger.info("Received messages", { count: messages.length });
          for (const msg of messages) {
            if (msg.message_id && recentMsgIds.has(msg.message_id)) {
              continue;
            }
            if (msg.message_id) {
              recentMsgIds.add(msg.message_id);
              if (recentMsgIds.size > MAX_MSG_IDS) {
                const iter = recentMsgIds.values();
                const toDelete = [];
                for (let i = 0; i < MAX_MSG_IDS / 2; i++) {
                  const { value } = iter.next();
                  if (value !== void 0) toDelete.push(value);
                }
                for (const id of toDelete) recentMsgIds.delete(id);
              }
            }
            callbacks.onMessage(msg).catch((err) => {
              const msg2 = err instanceof Error ? err.message : String(err);
              logger.error("Error processing message", { error: msg2, messageId: msg.message_id });
            });
          }
        }
        consecutiveFailures = 0;
      } catch (err) {
        if (controller.signal.aborted) {
          break;
        }
        consecutiveFailures++;
        const errorMsg = err instanceof Error ? err.message : String(err);
        logger.error("Monitor error", { error: errorMsg, consecutiveFailures });
        const backoff = consecutiveFailures >= BACKOFF_THRESHOLD ? BACKOFF_LONG_MS : BACKOFF_SHORT_MS;
        logger.info(`Backing off ${backoff}ms`, { consecutiveFailures });
        await sleep2(backoff, controller.signal);
      }
    }
    stopped = true;
    logger.info("Monitor stopped");
  }
  function stop() {
    if (!controller.signal.aborted) {
      logger.info("Stopping monitor...");
      controller.abort();
    }
  }
  return { run, stop };
}
function sleep2(ms, signal) {
  return new Promise((resolve3) => {
    if (signal?.aborted) {
      resolve3();
      return;
    }
    const timer = setTimeout(resolve3, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve3();
    }, { once: true });
  });
}
var SESSION_EXPIRED_ERRCODE, SESSION_EXPIRED_PAUSE_MS, BACKOFF_THRESHOLD, BACKOFF_LONG_MS, BACKOFF_SHORT_MS;
var init_monitor = __esm({
  "electron/backend/wechat_bridge/src/wechat/monitor.ts"() {
    init_sync_buf();
    init_logger();
    SESSION_EXPIRED_ERRCODE = -14;
    SESSION_EXPIRED_PAUSE_MS = 60 * 60 * 1e3;
    BACKOFF_THRESHOLD = 3;
    BACKOFF_LONG_MS = 3e4;
    BACKOFF_SHORT_MS = 3e3;
  }
});

// electron/backend/wechat_bridge/src/wechat/types.ts
var TypingStatus, UploadMediaType;
var init_types = __esm({
  "electron/backend/wechat_bridge/src/wechat/types.ts"() {
    TypingStatus = {
      TYPING: 1,
      CANCEL: 2
    };
    UploadMediaType = {
      IMAGE: 1,
      VIDEO: 2,
      FILE: 3,
      VOICE: 4
    };
  }
});

// electron/backend/wechat_bridge/src/wechat/crypto.ts
function aesEcbPaddedSize(size) {
  const block = 16;
  return Math.floor((size + block - 1) / block) * block;
}
function encryptAesEcb(key, plaintext) {
  const cipher = (0, import_crypto.createCipheriv)("aes-128-ecb", key, null);
  return Buffer.concat([cipher.update(plaintext), cipher.final()]);
}
function decryptAesEcb(key, ciphertext) {
  const decipher = (0, import_crypto.createDecipheriv)("aes-128-ecb", key, null);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
}
var import_crypto;
var init_crypto = __esm({
  "electron/backend/wechat_bridge/src/wechat/crypto.ts"() {
    import_crypto = require("crypto");
  }
});

// electron/backend/wechat_bridge/src/wechat/upload.ts
function isImageFile(filePath) {
  return IMAGE_EXTENSIONS.has((0, import_node_path6.extname)(filePath).toLowerCase());
}
async function uploadFile(api, toUserId, filePath) {
  const stat = (0, import_node_fs4.statSync)(filePath);
  if (stat.size > MAX_FILE_SIZE) {
    throw new Error(`\u6587\u4EF6\u8FC7\u5927 (${(stat.size / 1024 / 1024).toFixed(1)}MB)\uFF0C\u6700\u5927\u652F\u6301 25MB`);
  }
  const fileName = (0, import_node_path6.basename)(filePath);
  const isImage = isImageFile(filePath);
  const mediaType = isImage ? UploadMediaType.IMAGE : UploadMediaType.FILE;
  const plaintext = (0, import_node_fs4.readFileSync)(filePath);
  const rawSize = plaintext.length;
  const rawFileMd5 = (0, import_node_crypto.createHash)("md5").update(plaintext).digest("hex");
  const fileSize = aesEcbPaddedSize(rawSize);
  const fileKey = (0, import_node_crypto.randomBytes)(16).toString("hex");
  const aesKey = (0, import_node_crypto.randomBytes)(16);
  const aesKeyHex = aesKey.toString("hex");
  logger.info("Requesting upload URL", { fileName, rawSize, mediaType, toUserId });
  const uploadResp = await api.getUploadUrl({
    filekey: fileKey,
    media_type: mediaType,
    to_user_id: toUserId,
    rawsize: rawSize,
    rawfilemd5: rawFileMd5,
    filesize: fileSize,
    no_need_thumb: true,
    aeskey: aesKeyHex,
    base_info: {
      channel_version: "2.0.0",
      bot_agent: "wechat-claude-code"
    }
  });
  logger.info("Upload URL response", { uploadResp });
  if (!uploadResp.upload_full_url && !uploadResp.upload_param) {
    throw new Error(`\u83B7\u53D6\u4E0A\u4F20\u5730\u5740\u5931\u8D25: ${JSON.stringify(uploadResp)}`);
  }
  const encrypted = encryptAesEcb(aesKey, plaintext);
  let uploadUrl;
  if (uploadResp.upload_full_url) {
    uploadUrl = uploadResp.upload_full_url;
  } else {
    uploadUrl = `${CDN_BASE_URL}/upload?encrypted_query_param=${encodeURIComponent(uploadResp.upload_param)}&filekey=${fileKey}`;
  }
  logger.info("Uploading to CDN", { uploadUrl, encryptedSize: encrypted.length });
  const encryptQueryParam = await uploadToCdn(uploadUrl, encrypted);
  logger.info("CDN upload succeeded", { fileName });
  return {
    mediaType: isImage ? "image" : "file",
    encryptQueryParam,
    aesKeyHex,
    fileName,
    fileSize,
    rawSize
  };
}
async function uploadToCdn(url, encrypted) {
  const MAX_RETRIES = 3;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6e4);
    try {
      const res = await fetch(url, {
        method: "POST",
        body: new Uint8Array(encrypted),
        signal: controller.signal,
        headers: { "Content-Type": "application/octet-stream" }
      });
      if (res.status >= 400 && res.status < 500) {
        const text = await res.text();
        throw new Error(`CDN \u4E0A\u4F20\u5931\u8D25 (4xx): ${res.status} ${text.slice(0, 200)}`);
      }
      if (res.status >= 500) {
        logger.warn("CDN upload 5xx, retrying", { status: res.status, attempt });
        continue;
      }
      const param = res.headers.get("x-encrypted-param");
      if (!param) {
        throw new Error("CDN \u4E0A\u4F20\u6210\u529F\u4F46\u672A\u8FD4\u56DE x-encrypted-param");
      }
      return param;
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        throw new Error("CDN \u4E0A\u4F20\u8D85\u65F6");
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error("CDN \u4E0A\u4F20\u5931\u8D25: \u591A\u6B21\u91CD\u8BD5\u540E\u4ECD\u5931\u8D25");
}
var import_node_crypto, import_node_fs4, import_node_path6, MAX_FILE_SIZE, IMAGE_EXTENSIONS;
var init_upload = __esm({
  "electron/backend/wechat_bridge/src/wechat/upload.ts"() {
    import_node_crypto = require("node:crypto");
    import_node_fs4 = require("node:fs");
    import_node_path6 = require("node:path");
    init_crypto();
    init_types();
    init_constants();
    init_logger();
    MAX_FILE_SIZE = 25 * 1024 * 1024;
    IMAGE_EXTENSIONS = /* @__PURE__ */ new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"]);
  }
});

// electron/backend/wechat_bridge/src/wechat/send.ts
function createSender(api, botAccountId) {
  let clientCounter = 0;
  const typingTicketCache = /* @__PURE__ */ new Map();
  const TICKET_TTL = 24 * 60 * 60 * 1e3;
  function generateClientId() {
    return `wcc-${Date.now()}-${++clientCounter}`;
  }
  async function getTypingTicket(userId, contextToken) {
    const cached = typingTicketCache.get(userId);
    if (cached && Date.now() - cached.fetchedAt < TICKET_TTL) {
      return cached.ticket;
    }
    try {
      const resp = await api.getConfig(userId, contextToken);
      if (resp.ret === 0 && resp.typing_ticket) {
        typingTicketCache.set(userId, { ticket: resp.typing_ticket, fetchedAt: Date.now() });
        return resp.typing_ticket;
      }
      logger.warn("getConfig returned no typing_ticket", { ret: resp.ret });
    } catch (err) {
      logger.warn("getConfig failed", { err: err instanceof Error ? err.message : String(err) });
    }
    return "";
  }
  function startTyping(toUserId, contextToken) {
    let cancelled = false;
    (async () => {
      const ticket = await getTypingTicket(toUserId, contextToken);
      if (!ticket || cancelled) return;
      try {
        await api.sendTyping({
          ilink_user_id: toUserId,
          typing_ticket: ticket,
          status: TypingStatus.TYPING
        });
      } catch (err) {
        logger.debug("sendTyping start failed", { err: err instanceof Error ? err.message : String(err) });
        return;
      }
      while (!cancelled) {
        await new Promise((r) => setTimeout(r, TYPING_KEEPALIVE_MS));
        if (cancelled) break;
        try {
          await api.sendTyping({
            ilink_user_id: toUserId,
            typing_ticket: ticket,
            status: TypingStatus.TYPING
          });
        } catch {
          break;
        }
      }
      if (!ticket) return;
      try {
        await api.sendTyping({
          ilink_user_id: toUserId,
          typing_ticket: ticket,
          status: TypingStatus.CANCEL
        });
      } catch {
      }
    })();
    return () => {
      cancelled = true;
    };
  }
  async function sendText(toUserId, contextToken, text) {
    const clientId = generateClientId();
    const items = [
      {
        type: 1 /* TEXT */,
        text_item: { text }
      }
    ];
    const msg = {
      from_user_id: botAccountId,
      to_user_id: toUserId,
      client_id: clientId,
      message_type: 2 /* BOT */,
      message_state: 2 /* FINISH */,
      context_token: contextToken,
      item_list: items
    };
    logger.info("Sending text message", { toUserId, clientId, textLength: text.length });
    await api.sendMessage({ msg });
    logger.info("Text message sent", { toUserId, clientId });
  }
  async function sendFile(toUserId, contextToken, filePath) {
    const resolved = (0, import_node_path7.resolve)(filePath.replace(/^~/, process.env.HOME || ""));
    if (!(0, import_node_fs5.existsSync)(resolved)) {
      await sendText(toUserId, contextToken, `\u6587\u4EF6\u4E0D\u5B58\u5728: ${resolved}`);
      return;
    }
    try {
      const media = await uploadFile(api, toUserId, resolved);
      const clientId = generateClientId();
      const aesKeyBase64 = Buffer.from(media.aesKeyHex).toString("base64");
      let item;
      if (media.mediaType === "image") {
        item = {
          type: 2 /* IMAGE */,
          image_item: {
            media: {
              encrypt_query_param: media.encryptQueryParam,
              aes_key: aesKeyBase64,
              encrypt_type: 1
            },
            mid_size: media.fileSize
          }
        };
      } else {
        item = {
          type: 4 /* FILE */,
          file_item: {
            media: {
              encrypt_query_param: media.encryptQueryParam,
              aes_key: aesKeyBase64,
              encrypt_type: 1
            },
            file_name: media.fileName,
            len: String(media.rawSize)
          }
        };
      }
      const msg = {
        from_user_id: botAccountId,
        to_user_id: toUserId,
        client_id: clientId,
        message_type: 2 /* BOT */,
        message_state: 2 /* FINISH */,
        context_token: contextToken,
        item_list: [item]
      };
      logger.info("Sending file message", { toUserId, clientId, fileName: media.fileName, mediaType: media.mediaType });
      await api.sendMessage({ msg });
      logger.info("File message sent", { toUserId, clientId, fileName: media.fileName });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.error("Failed to send file", { filePath: resolved, error: msg });
      if (!msg.includes("rate-limited")) {
        await sendText(toUserId, contextToken, `\u53D1\u9001\u6587\u4EF6\u5931\u8D25: ${msg}`);
      }
      throw err;
    }
  }
  return { sendText, startTyping, sendFile };
}
var import_node_fs5, import_node_path7, TYPING_KEEPALIVE_MS;
var init_send = __esm({
  "electron/backend/wechat_bridge/src/wechat/send.ts"() {
    import_node_fs5 = require("node:fs");
    import_node_path7 = require("node:path");
    init_types();
    init_upload();
    init_logger();
    TYPING_KEEPALIVE_MS = 5e3;
  }
});

// electron/backend/wechat_bridge/src/wechat/cdn.ts
function buildCdnDownloadUrl(encryptQueryParam) {
  if (!/^[A-Za-z0-9%=&+._~\-/]+$/.test(encryptQueryParam)) {
    throw new Error("Invalid CDN query parameter");
  }
  return `${CDN_BASE_URL}/download?encrypted_query_param=${encodeURIComponent(encryptQueryParam)}`;
}
async function downloadAndDecrypt(encryptQueryParam, aesKeyBase64) {
  const url = buildCdnDownloadUrl(encryptQueryParam);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3e4);
  let response;
  try {
    response = await fetch(url, { signal: controller.signal });
  } catch (err) {
    clearTimeout(timer);
    throw new Error(`CDN download failed: ${err instanceof Error ? err.message : String(err)}`);
  }
  clearTimeout(timer);
  if (!response.ok) {
    throw new Error(`CDN download failed: ${response.status} ${response.statusText}`);
  }
  const encrypted = Buffer.from(await response.arrayBuffer());
  let aesKey;
  const raw = Buffer.from(aesKeyBase64, "base64");
  if (raw.length === 16) {
    aesKey = raw;
  } else {
    const hexStr = raw.toString("utf-8");
    aesKey = Buffer.from(hexStr, "hex");
  }
  const decrypted = decryptAesEcb(aesKey, encrypted);
  logger.info("CDN download and decrypt succeeded", { size: decrypted.length });
  return decrypted;
}
var init_cdn = __esm({
  "electron/backend/wechat_bridge/src/wechat/cdn.ts"() {
    init_crypto();
    init_logger();
    init_constants();
  }
});

// electron/backend/wechat_bridge/src/wechat/media.ts
function detectMimeType(data) {
  if (data[0] === 137 && data[1] === 80) return "image/png";
  if (data[0] === 255 && data[1] === 216) return "image/jpeg";
  if (data[0] === 71 && data[1] === 73) return "image/gif";
  if (data[0] === 82 && data[1] === 73) return "image/webp";
  if (data[0] === 66 && data[1] === 77) return "image/bmp";
  return "image/jpeg";
}
function getImageCdnData(imageItem) {
  if (imageItem.cdn_media?.aes_key && imageItem.cdn_media?.encrypt_query_param) {
    return {
      aesKey: imageItem.cdn_media.aes_key,
      encryptQueryParam: imageItem.cdn_media.encrypt_query_param
    };
  }
  if (imageItem.media?.encrypt_query_param && (imageItem.media.aes_key || imageItem.aeskey)) {
    return {
      aesKey: imageItem.media.aes_key ?? imageItem.aeskey,
      encryptQueryParam: imageItem.media.encrypt_query_param
    };
  }
  logger.warn("Image item has no usable CDN data", {
    hasCdnMedia: !!imageItem.cdn_media,
    hasAeskey: !!imageItem.aeskey,
    hasMedia: !!imageItem.media
  });
  return null;
}
async function downloadImage(item) {
  const imageItem = item.image_item;
  if (!imageItem) {
    return null;
  }
  const cdnData = getImageCdnData(imageItem);
  if (!cdnData) {
    return null;
  }
  try {
    const decrypted = await downloadAndDecrypt(cdnData.encryptQueryParam, cdnData.aesKey);
    const mimeType = detectMimeType(decrypted);
    const base64 = decrypted.toString("base64");
    const dataUri = `data:${mimeType};base64,${base64}`;
    logger.info("Image downloaded and decrypted", { size: decrypted.length });
    return dataUri;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.warn("Failed to download image", { error: msg });
    return null;
  }
}
function extractText(item) {
  if (item.text_item?.text) return item.text_item.text;
  if (item.voice_item?.text) return item.voice_item.text;
  if (item.file_item?.file_name) return `[\u7528\u6237\u53D1\u9001\u4E86\u6587\u4EF6: ${item.file_item.file_name}]`;
  if (item.type === 5 /* VIDEO */) return "[\u7528\u6237\u53D1\u9001\u4E86\u89C6\u9891]";
  return "";
}
function extractFirstImageUrl(items) {
  return items?.find((item) => item.type === 2 /* IMAGE */);
}
function extractFirstFileItem(items) {
  return items?.find((item) => item.type === 4 /* FILE */);
}
async function downloadFile(item) {
  const fileItem = item.file_item;
  if (!fileItem) return null;
  let aesKey;
  let encryptQueryParam;
  if (fileItem.media?.encrypt_query_param) {
    encryptQueryParam = fileItem.media.encrypt_query_param;
    aesKey = fileItem.media.aes_key;
  } else if (fileItem.cdn_media?.encrypt_query_param) {
    encryptQueryParam = fileItem.cdn_media.encrypt_query_param;
    aesKey = fileItem.cdn_media.aes_key;
  }
  if (!encryptQueryParam || !aesKey) {
    logger.warn("File item has no usable CDN data");
    return null;
  }
  try {
    const decrypted = await downloadAndDecrypt(encryptQueryParam, aesKey);
    const tmpDir = import_node_path8.default.join(import_node_os2.default.tmpdir(), "wechat-claude-code");
    import_node_fs6.default.mkdirSync(tmpDir, { recursive: true });
    const fileName = fileItem.file_name || `file-${Date.now()}.bin`;
    const filePath = import_node_path8.default.join(tmpDir, fileName);
    import_node_fs6.default.writeFileSync(filePath, decrypted);
    logger.info("File downloaded and saved", { path: filePath, size: decrypted.length, name: fileName });
    return filePath;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.warn("Failed to download file", { error: msg });
    return null;
  }
}
var import_node_path8, import_node_os2, import_node_fs6;
var init_media = __esm({
  "electron/backend/wechat_bridge/src/wechat/media.ts"() {
    import_node_path8 = __toESM(require("node:path"));
    import_node_os2 = __toESM(require("node:os"));
    import_node_fs6 = __toESM(require("node:fs"));
    init_types();
    init_cdn();
    init_logger();
  }
});

// electron/backend/wechat_bridge/src/session.ts
function createSessionStore() {
  function getSessionPath(accountId) {
    validateAccountId(accountId);
    return (0, import_node_path9.join)(SESSIONS_DIR, `${accountId}.json`);
  }
  function load(accountId) {
    validateAccountId(accountId);
    const session = loadJson(getSessionPath(accountId), {
      workingDirectory: DEFAULT_WORKING_DIR,
      state: "idle",
      chatHistory: [],
      maxHistoryLength: DEFAULT_MAX_HISTORY
    });
    if (!session.chatHistory) {
      session.chatHistory = [];
    }
    if (!session.maxHistoryLength) {
      session.maxHistoryLength = DEFAULT_MAX_HISTORY;
    }
    return session;
  }
  function save(accountId, session) {
    (0, import_node_fs7.mkdirSync)(SESSIONS_DIR, { recursive: true });
    const maxLen = session.maxHistoryLength || DEFAULT_MAX_HISTORY;
    if (session.chatHistory.length > maxLen) {
      session.chatHistory = session.chatHistory.slice(-maxLen);
    }
    saveJson(getSessionPath(accountId), session);
  }
  function clear(accountId, currentSession) {
    const session = {
      sdkSessionId: void 0,
      // explicitly clear so Object.assign removes it
      previousSdkSessionId: void 0,
      workingDirectory: currentSession?.workingDirectory ?? DEFAULT_WORKING_DIR,
      model: currentSession?.model,
      state: "idle",
      chatHistory: [],
      maxHistoryLength: currentSession?.maxHistoryLength || DEFAULT_MAX_HISTORY
    };
    save(accountId, session);
    return session;
  }
  function addChatMessage(session, role, content) {
    if (!session.chatHistory) {
      session.chatHistory = [];
    }
    session.chatHistory.push({
      role,
      content,
      timestamp: Date.now()
    });
    const maxLen = session.maxHistoryLength || DEFAULT_MAX_HISTORY;
    if (session.chatHistory.length > maxLen) {
      session.chatHistory = session.chatHistory.slice(-maxLen);
    }
  }
  function getChatHistoryText(session, limit) {
    const history = session.chatHistory || [];
    const messages = limit ? history.slice(-limit) : history;
    if (messages.length === 0) {
      return "\u6682\u65E0\u5BF9\u8BDD\u8BB0\u5F55";
    }
    const lines = [];
    for (const msg of messages) {
      const time = new Date(msg.timestamp).toLocaleString("zh-CN");
      const role = msg.role === "user" ? "\u7528\u6237" : "Claude";
      lines.push(`[${time}] ${role}:`);
      lines.push(msg.content);
      lines.push("");
    }
    return lines.join("\n");
  }
  return { load, save, clear, addChatMessage, getChatHistoryText };
}
var import_node_fs7, import_node_path9, SESSIONS_DIR, DEFAULT_MAX_HISTORY;
var init_session = __esm({
  "electron/backend/wechat_bridge/src/session.ts"() {
    init_store();
    import_node_fs7 = require("node:fs");
    init_constants();
    import_node_path9 = require("node:path");
    SESSIONS_DIR = (0, import_node_path9.join)(DATA_DIR, "sessions");
    DEFAULT_MAX_HISTORY = 100;
  }
});

// electron/backend/wechat_bridge/src/claude/skill-scanner.ts
function parseSkillMd(filePath) {
  try {
    const content = (0, import_node_fs8.readFileSync)(filePath, "utf-8");
    const match = content.match(/^---\n([\s\S]*?)\n---/);
    if (!match) return null;
    const frontmatter = match[1];
    const nameMatch = frontmatter.match(/^name:\s*(.+)$/m);
    const descMatch = frontmatter.match(/^description:\s*(.+)$/m);
    if (!nameMatch) return null;
    return {
      name: nameMatch[1].trim().replace(/^["']|["']$/g, ""),
      description: descMatch ? descMatch[1].trim().replace(/^["']|["']$/g, "") : ""
    };
  } catch {
    logger.warn(`Failed to read SKILL.md: ${filePath}`);
    return null;
  }
}
function scanDirectory(baseDir, depth = 2) {
  const skills = [];
  if (!(0, import_node_fs8.existsSync)(baseDir)) return skills;
  let entries;
  try {
    entries = (0, import_node_fs8.readdirSync)(baseDir, { withFileTypes: true });
  } catch {
    return skills;
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const fullPath = (0, import_node_path10.join)(baseDir, entry.name);
    if (depth > 1) {
      skills.push(...scanDirectory(fullPath, depth - 1));
    }
    const skillFile = (0, import_node_path10.join)(fullPath, "SKILL.md");
    if ((0, import_node_fs8.existsSync)(skillFile)) {
      const info = parseSkillMd(skillFile);
      if (info) {
        skills.push({ ...info, path: fullPath });
      }
    }
  }
  return skills;
}
function scanAllSkills() {
  const home = (0, import_node_os3.homedir)();
  const claudeDir = (0, import_node_path10.join)(home, ".claude");
  const skills = [];
  const seen = /* @__PURE__ */ new Set();
  const userSkillsDir = (0, import_node_path10.join)(claudeDir, "skills");
  for (const skill of scanDirectory(userSkillsDir, 1)) {
    if (!seen.has(skill.name)) {
      seen.add(skill.name);
      skills.push(skill);
    }
  }
  const pluginsCacheDir = (0, import_node_path10.join)(claudeDir, "plugins", "cache");
  if ((0, import_node_fs8.existsSync)(pluginsCacheDir)) {
    let cacheEntries;
    try {
      cacheEntries = (0, import_node_fs8.readdirSync)(pluginsCacheDir, { withFileTypes: true });
    } catch {
      cacheEntries = [];
    }
    for (const cacheEntry of cacheEntries) {
      if (!cacheEntry.isDirectory()) continue;
      const cacheDir = (0, import_node_path10.join)(pluginsCacheDir, cacheEntry.name);
      const pluginSkillsDir = (0, import_node_path10.join)(cacheDir, "skills");
      for (const skill of scanDirectory(pluginSkillsDir, 1)) {
        if (!seen.has(skill.name)) {
          seen.add(skill.name);
          skills.push(skill);
        }
      }
      const superpowersSkillsDir = (0, import_node_path10.join)(cacheDir, "superpowers", "skills");
      for (const skill of scanDirectory(superpowersSkillsDir, 1)) {
        if (!seen.has(skill.name)) {
          seen.add(skill.name);
          skills.push(skill);
        }
      }
    }
  }
  logger.info(`Scanned ${skills.length} skills`);
  return skills;
}
function findSkill(skills, name) {
  const lower = name.toLowerCase();
  return skills.find(
    (s) => s.name.toLowerCase() === lower || s.name.toLowerCase().replace(/\s+/g, "-") === lower
  );
}
var import_node_fs8, import_node_path10, import_node_os3;
var init_skill_scanner = __esm({
  "electron/backend/wechat_bridge/src/claude/skill-scanner.ts"() {
    import_node_fs8 = require("node:fs");
    import_node_path10 = require("node:path");
    import_node_os3 = require("node:os");
    init_logger();
  }
});

// electron/backend/wechat_bridge/src/config.ts
function loadConfig() {
  try {
    const content = (0, import_node_fs9.readFileSync)(CONFIG_PATH, "utf-8");
    const parsed = JSON.parse(content);
    const config = {
      workingDirectory: parsed.workingDirectory || DEFAULT_CONFIG.workingDirectory,
      model: parsed.model,
      systemPrompt: parsed.systemPrompt
    };
    (0, import_node_fs9.mkdirSync)(config.workingDirectory, { recursive: true });
    return config;
  } catch {
    const config = { ...DEFAULT_CONFIG };
    (0, import_node_fs9.mkdirSync)(config.workingDirectory, { recursive: true });
    return config;
  }
}
function saveConfig(config) {
  (0, import_node_fs9.mkdirSync)(CONFIG_DIR, { recursive: true });
  const data = {
    workingDirectory: config.workingDirectory
  };
  if (config.model) data.model = config.model;
  if (config.systemPrompt) data.systemPrompt = config.systemPrompt;
  (0, import_node_fs9.writeFileSync)(CONFIG_PATH, JSON.stringify(data, null, 2) + "\n", "utf-8");
  if (process.platform !== "win32") {
    (0, import_node_fs9.chmodSync)(CONFIG_PATH, 384);
  }
}
var import_node_fs9, import_node_path11, CONFIG_DIR, CONFIG_PATH, DEFAULT_CONFIG;
var init_config = __esm({
  "electron/backend/wechat_bridge/src/config.ts"() {
    import_node_fs9 = require("node:fs");
    import_node_path11 = require("node:path");
    init_constants();
    CONFIG_DIR = DATA_DIR;
    CONFIG_PATH = (0, import_node_path11.join)(CONFIG_DIR, "config.json");
    DEFAULT_CONFIG = {
      workingDirectory: DEFAULT_WORKING_DIR
    };
  }
});

// electron/backend/wechat_bridge/src/commands/handlers.ts
function getSkills() {
  const now = Date.now();
  if (!cachedSkills || now - lastScanTime > CACHE_TTL) {
    cachedSkills = scanAllSkills();
    lastScanTime = now;
  }
  return cachedSkills;
}
function invalidateSkillCache() {
  cachedSkills = null;
}
function handleHelp(_args) {
  return { reply: HELP_TEXT, handled: true };
}
function handleClear(ctx) {
  const newSession = ctx.clearSession();
  Object.assign(ctx.session, newSession);
  return { reply: "\u2705 \u4F1A\u8BDD\u5DF2\u6E05\u9664\uFF0C\u4E0B\u6B21\u6D88\u606F\u5C06\u5F00\u59CB\u65B0\u4F1A\u8BDD\u3002", handled: true };
}
function handleCwd(ctx, args) {
  if (!args) {
    return { reply: `\u5F53\u524D\u5DE5\u4F5C\u76EE\u5F55: ${ctx.session.workingDirectory}
\u7528\u6CD5: /cwd <\u8DEF\u5F84>`, handled: true };
  }
  ctx.updateSession({ workingDirectory: args });
  return { reply: `\u2705 \u5DE5\u4F5C\u76EE\u5F55\u5DF2\u5207\u6362\u4E3A: ${args}`, handled: true };
}
function handleModel(ctx, args) {
  if (!args) {
    return { reply: "\u7528\u6CD5: /model <\u6A21\u578B\u540D\u79F0>\n\u4F8B: /model claude-sonnet-4-6", handled: true };
  }
  ctx.updateSession({ model: args });
  return { reply: `\u2705 \u6A21\u578B\u5DF2\u5207\u6362\u4E3A: ${args}`, handled: true };
}
function handleStatus(ctx) {
  const s = ctx.session;
  const lines = [
    "\u{1F4CA} \u4F1A\u8BDD\u72B6\u6001",
    "",
    `\u5DE5\u4F5C\u76EE\u5F55: ${s.workingDirectory}`,
    `\u6A21\u578B: ${s.model ?? "\u9ED8\u8BA4"}`,
    `\u4F1A\u8BDDID: ${s.sdkSessionId ?? "\u65E0"}`,
    `\u72B6\u6001: ${s.state}`
  ];
  return { reply: lines.join("\n"), handled: true };
}
function handleSkills(args) {
  invalidateSkillCache();
  const skills = getSkills();
  if (skills.length === 0) {
    return { reply: "\u672A\u627E\u5230\u5DF2\u5B89\u88C5\u7684 skill\u3002", handled: true };
  }
  const showFull = args.trim().toLowerCase() === "full";
  if (showFull) {
    const lines2 = skills.map((s) => `/${s.name}
   ${s.description}`);
    return { reply: `\u{1F4CB} \u5DF2\u5B89\u88C5\u7684 Skill (${skills.length}):

${lines2.join("\n\n")}`, handled: true };
  }
  const lines = skills.map((s) => `/${s.name}`);
  return { reply: `\u{1F4CB} \u5DF2\u5B89\u88C5\u7684 Skill (${skills.length}):

${lines.join("\n")}

\u4F7F\u7528 /skills full \u67E5\u770B\u5B8C\u6574\u63CF\u8FF0`, handled: true };
}
function handleHistory(ctx, args) {
  const limit = args ? parseInt(args, 10) : 20;
  if (isNaN(limit) || limit <= 0) {
    return { reply: "\u7528\u6CD5: /history [\u6570\u91CF]\n\u4F8B: /history 50\uFF08\u663E\u793A\u6700\u8FD150\u6761\u5BF9\u8BDD\uFF09", handled: true };
  }
  const effectiveLimit = Math.min(limit, MAX_HISTORY_LIMIT);
  const historyText = ctx.getChatHistoryText?.(effectiveLimit) || "\u6682\u65E0\u5BF9\u8BDD\u8BB0\u5F55";
  return { reply: `\u{1F4DD} \u5BF9\u8BDD\u8BB0\u5F55\uFF08\u6700\u8FD1${effectiveLimit}\u6761\uFF09:

${historyText}`, handled: true };
}
function handleReset(ctx) {
  const newSession = ctx.clearSession();
  newSession.workingDirectory = DEFAULT_WORKING_DIR;
  Object.assign(ctx.session, newSession);
  return { reply: "\u2705 \u4F1A\u8BDD\u5DF2\u5B8C\u5168\u91CD\u7F6E\uFF0C\u6240\u6709\u8BBE\u7F6E\u6062\u590D\u9ED8\u8BA4\u3002", handled: true };
}
function handleCompact(ctx) {
  const currentSessionId = ctx.session.sdkSessionId;
  if (!currentSessionId) {
    return { reply: "\u2139\uFE0F \u5F53\u524D\u6CA1\u6709\u6D3B\u52A8\u7684 SDK \u4F1A\u8BDD\uFF0C\u65E0\u9700\u538B\u7F29\u3002", handled: true };
  }
  ctx.updateSession({
    previousSdkSessionId: currentSessionId,
    sdkSessionId: void 0
  });
  return {
    reply: "\u2705 \u4E0A\u4E0B\u6587\u5DF2\u538B\u7F29\n\n\u4E0B\u6B21\u6D88\u606F\u5C06\u5F00\u59CB\u65B0\u7684 SDK \u4F1A\u8BDD\uFF08token \u6E05\u96F6\uFF09\n\u804A\u5929\u5386\u53F2\u5DF2\u4FDD\u7559\uFF0C\u53EF\u7528 /history \u67E5\u770B",
    handled: true
  };
}
function handleUndo(ctx, args) {
  const count = args ? parseInt(args, 10) : 1;
  if (isNaN(count) || count <= 0) {
    return { reply: "\u7528\u6CD5: /undo [\u6570\u91CF]\n\u4F8B: /undo 2\uFF08\u64A4\u9500\u6700\u8FD12\u6761\u5BF9\u8BDD\uFF09", handled: true };
  }
  const history = ctx.session.chatHistory || [];
  if (history.length === 0) {
    return { reply: "\u26A0\uFE0F \u6CA1\u6709\u5BF9\u8BDD\u8BB0\u5F55\u53EF\u64A4\u9500", handled: true };
  }
  const actualCount = Math.min(count, history.length);
  ctx.session.chatHistory = history.slice(0, -actualCount);
  ctx.updateSession({ chatHistory: ctx.session.chatHistory });
  return { reply: `\u2705 \u5DF2\u64A4\u9500\u6700\u8FD1 ${actualCount} \u6761\u5BF9\u8BDD`, handled: true };
}
function handleVersion() {
  try {
    const __dirname = (0, import_node_url.fileURLToPath)(new URL(".", import_meta.url));
    const pkg = JSON.parse((0, import_node_fs10.readFileSync)((0, import_node_path12.join)(__dirname, "..", "..", "package.json"), "utf-8"));
    const version = pkg.version || "unknown";
    return { reply: `wechat-claude-code v${version}`, handled: true };
  } catch {
    return { reply: "wechat-claude-code (version unknown)", handled: true };
  }
}
function handlePrompt(_ctx, args) {
  const config = loadConfig();
  if (!args) {
    const current = config.systemPrompt;
    if (current) {
      return { reply: `\u{1F4DD} \u5F53\u524D\u7CFB\u7EDF\u63D0\u793A\u8BCD:
${current}

\u7528\u6CD5:
/prompt <\u63D0\u793A\u8BCD>  \u2014 \u8BBE\u7F6E
/prompt clear   \u2014 \u6E05\u9664`, handled: true };
    }
    return { reply: "\u{1F4DD} \u6682\u65E0\u7CFB\u7EDF\u63D0\u793A\u8BCD\n\n\u7528\u6CD5: /prompt <\u63D0\u793A\u8BCD>\n\u4F8B: /prompt \u7528\u4E2D\u6587\u56DE\u7B54\u6211", handled: true };
  }
  if (args.trim().toLowerCase() === "clear") {
    config.systemPrompt = void 0;
    saveConfig(config);
    return { reply: "\u2705 \u7CFB\u7EDF\u63D0\u793A\u8BCD\u5DF2\u6E05\u9664", handled: true };
  }
  config.systemPrompt = args.trim();
  saveConfig(config);
  return { reply: `\u2705 \u7CFB\u7EDF\u63D0\u793A\u8BCD\u5DF2\u8BBE\u7F6E:
${config.systemPrompt}`, handled: true };
}
function handleSend(ctx, args) {
  if (!args) {
    return { reply: "\u7528\u6CD5: /send <\u6587\u4EF6\u8DEF\u5F84>\n\u4F8B: /send ~/Documents/report.pdf\n     /send ./chart.png", handled: true };
  }
  const resolved = args.startsWith("/") ? args : (0, import_node_path12.resolve)(ctx.session.workingDirectory, args.replace(/^~/, (0, import_node_os4.homedir)()));
  if (!(0, import_node_fs10.existsSync)(resolved)) {
    return { reply: `\u6587\u4EF6\u4E0D\u5B58\u5728: ${resolved}`, handled: true };
  }
  const stat = (0, import_node_fs10.statSync)(resolved);
  if (stat.isDirectory()) {
    return { reply: `\u8FD9\u662F\u4E00\u4E2A\u76EE\u5F55\uFF0C\u8BF7\u6307\u5B9A\u6587\u4EF6: ${resolved}`, handled: true };
  }
  if (stat.size > 25 * 1024 * 1024) {
    return { reply: `\u6587\u4EF6\u8FC7\u5927 (${(stat.size / 1024 / 1024).toFixed(1)}MB)\uFF0C\u6700\u5927\u652F\u6301 25MB`, handled: true };
  }
  return { handled: true, sendFile: resolved };
}
function handleUnknown(cmd, args) {
  const skills = getSkills();
  const skill = findSkill(skills, cmd);
  if (skill) {
    const prompt = args ? `Use the ${skill.name} skill: ${args}` : `Use the ${skill.name} skill`;
    return { handled: true, claudePrompt: prompt };
  }
  return {
    handled: true,
    reply: `\u672A\u627E\u5230 skill: ${cmd}
\u8F93\u5165 /skills \u67E5\u770B\u53EF\u7528\u5217\u8868`
  };
}
var import_node_fs10, import_node_path12, import_node_os4, import_node_url, import_meta, HELP_TEXT, cachedSkills, lastScanTime, CACHE_TTL, MAX_HISTORY_LIMIT;
var init_handlers = __esm({
  "electron/backend/wechat_bridge/src/commands/handlers.ts"() {
    init_skill_scanner();
    init_config();
    init_constants();
    import_node_fs10 = require("node:fs");
    import_node_path12 = require("node:path");
    import_node_os4 = require("node:os");
    import_node_url = require("node:url");
    import_meta = {};
    HELP_TEXT = `\u53EF\u7528\u547D\u4EE4\uFF1A

\u4F1A\u8BDD\u7BA1\u7406\uFF1A
  /help             \u663E\u793A\u5E2E\u52A9
  /stop             \u505C\u6B62\u5F53\u524D\u5BF9\u8BDD\u5E76\u6E05\u7A7A\u6392\u961F\u6D88\u606F
  /clear            \u6E05\u9664\u5F53\u524D\u4F1A\u8BDD
  /reset            \u5B8C\u5168\u91CD\u7F6E\uFF08\u5305\u62EC\u5DE5\u4F5C\u76EE\u5F55\u7B49\u8BBE\u7F6E\uFF09
  /status           \u67E5\u770B\u5F53\u524D\u4F1A\u8BDD\u72B6\u6001
  /compact          \u538B\u7F29\u4E0A\u4E0B\u6587\uFF08\u5F00\u59CB\u65B0 SDK \u4F1A\u8BDD\uFF0C\u4FDD\u7559\u5386\u53F2\uFF09
  /history [\u6570\u91CF]   \u67E5\u770B\u5BF9\u8BDD\u8BB0\u5F55\uFF08\u9ED8\u8BA4\u6700\u8FD120\u6761\uFF09
  /undo [\u6570\u91CF]      \u64A4\u9500\u6700\u8FD1\u5BF9\u8BDD\uFF08\u9ED8\u8BA41\u6761\uFF09

\u6587\u4EF6\uFF1A
  /send <\u8DEF\u5F84>      \u53D1\u9001\u672C\u5730\u6587\u4EF6\uFF08\u56FE\u7247\u76F4\u63A5\u663E\u793A\uFF0C\u5176\u4ED6\u6587\u4EF6\u4F5C\u4E3A\u9644\u4EF6\uFF09

\u914D\u7F6E\uFF1A
  /cwd [\u8DEF\u5F84]       \u67E5\u770B\u6216\u5207\u6362\u5DE5\u4F5C\u76EE\u5F55
  /model [\u540D\u79F0]     \u67E5\u770B\u6216\u5207\u6362 Claude \u6A21\u578B
  /prompt [\u5185\u5BB9]    \u67E5\u770B\u6216\u8BBE\u7F6E\u7CFB\u7EDF\u63D0\u793A\u8BCD\uFF08\u5168\u5C40\u751F\u6548\uFF09

\u5176\u4ED6\uFF1A
  /skills [full]    \u5217\u51FA\u5DF2\u5B89\u88C5\u7684 skill\uFF08full \u663E\u793A\u63CF\u8FF0\uFF09
  /version          \u67E5\u770B\u7248\u672C\u4FE1\u606F
  /<skill> [\u53C2\u6570]   \u89E6\u53D1\u5DF2\u5B89\u88C5\u7684 skill

\u76F4\u63A5\u8F93\u5165\u6587\u5B57\u5373\u53EF\u4E0E Claude Code \u5BF9\u8BDD`;
    cachedSkills = null;
    lastScanTime = 0;
    CACHE_TTL = 6e4;
    MAX_HISTORY_LIMIT = 100;
  }
});

// electron/backend/wechat_bridge/src/commands/router.ts
function routeCommand(ctx) {
  const text = ctx.text.trim();
  if (!text.startsWith("/")) {
    return { handled: false };
  }
  const spaceIdx = text.indexOf(" ");
  const cmd = (spaceIdx === -1 ? text.slice(1) : text.slice(1, spaceIdx)).toLowerCase();
  const args = spaceIdx === -1 ? "" : text.slice(spaceIdx + 1).trim();
  logger.info(`Slash command: /${cmd} ${args}`.trimEnd());
  switch (cmd) {
    case "help":
      return handleHelp(args);
    case "clear":
      return handleClear(ctx);
    case "reset":
      return handleReset(ctx);
    case "cwd":
      return handleCwd(ctx, args);
    case "model":
      return handleModel(ctx, args);
    case "prompt":
      return handlePrompt(ctx, args);
    case "status":
      return handleStatus(ctx);
    case "skills":
      return handleSkills(args);
    case "history":
      return handleHistory(ctx, args);
    case "undo":
      return handleUndo(ctx, args);
    case "compact":
      return handleCompact(ctx);
    case "send":
      return handleSend(ctx, args);
    case "version":
    case "v":
      return handleVersion();
    default:
      return handleUnknown(cmd, args);
  }
}
var init_router = __esm({
  "electron/backend/wechat_bridge/src/commands/router.ts"() {
    init_logger();
    init_handlers();
  }
});

// electron/backend/wechat_bridge/src/claude/provider.ts
function handleAgUiEvent(ev, state, callbacks) {
  switch (ev?.type) {
    case "TEXT_MESSAGE_START": {
      state.messageId = ev.messageId || state.messageId;
      break;
    }
    case "TEXT_MESSAGE_CONTENT": {
      const delta = ev.delta || "";
      if (/^Operation interrupted:/.test(delta)) break;
      if (delta) {
        state.textParts.push(delta);
        callbacks.onText?.(delta);
      }
      break;
    }
    case "TEXT_MESSAGE_END": {
      break;
    }
    case "RUN_ERROR": {
      state.errorMessage = ev.message || ev.detail || "RUN_ERROR";
      logger.error("AG-UI run error", { message: state.errorMessage });
      state.finished = true;
      callbacks.onTurnEnd?.("error");
      return true;
    }
    case "RUN_FINISHED": {
      state.finished = true;
      callbacks.onTurnEnd?.("end_turn");
      return true;
    }
    default:
      break;
  }
  return false;
}
function resolveAguiPort() {
  const p = Number(process.env.AGUI_PORT || 0);
  return Number.isFinite(p) && p > 0 ? p : 0;
}
async function claudeQuery(options) {
  const {
    prompt,
    threadId,
    model,
    images,
    onText,
    onTurnEnd,
    abortController,
    systemPrompt
  } = options;
  const port = resolveAguiPort();
  if (!port) {
    return { text: "", sessionId: "", error: "AGUI_PORT not available (backend not started)" };
  }
  if (!threadId) {
    return { text: "", sessionId: "", error: "claudeQuery: threadId is required (one Hermes thread per WeChat user)" };
  }
  const agThreadId = threadId;
  const runId = (0, import_node_crypto2.randomUUID)();
  const wireImages = (images || []).map((img, i) => ({
    alt: "",
    dataUrl: `data:${img.source.media_type};base64,${img.source.data}`,
    filename: `wechat_image_${i + 1}.${img.source.media_type.split("/")[1] || "png"}`
  }));
  const userContent = systemPrompt ? `${systemPrompt}

---

${prompt}` : prompt;
  const body = {
    method: "agent/run",
    threadId: agThreadId,
    runId,
    messages: [{ id: (0, import_node_crypto2.randomUUID)(), role: "user", content: userContent }],
    forwardedProps: {
      assistantId: process.env.WECHAT_BRIDGE_ASSISTANT_ID || "default",
      ...model ? { model } : {}
    },
    ...wireImages.length > 0 ? { images: wireImages } : {}
  };
  logger.info("Starting AG-UI run", {
    port,
    threadId: agThreadId,
    runId,
    textLength: prompt.length,
    hasImages: wireImages.length > 0
  });
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  abortController?.signal.addEventListener("abort", onAbort, { once: true });
  let fetchRes;
  try {
    fetchRes = await fetch(`http://127.0.0.1:${port}/api/ag-ui/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal: controller.signal
    });
  } catch (err) {
    abortController?.signal.removeEventListener("abort", onAbort);
    const msg = err instanceof Error ? err.message : String(err);
    return { text: "", sessionId: agThreadId, error: `Failed to reach agui-server: ${msg}` };
  }
  if (!fetchRes.ok || !fetchRes.body) {
    abortController?.signal.removeEventListener("abort", onAbort);
    return { text: "", sessionId: agThreadId, error: `agui-server HTTP ${fetchRes.status}` };
  }
  const QUERY_TIMEOUT_MS = 60 * 60 * 1e3;
  const streamReader = fetchRes.body.getReader();
  const timeoutId = setTimeout(() => {
    logger.warn("AG-UI run timed out, aborting");
    try {
      streamReader.cancel().catch(() => {
      });
    } catch {
    }
  }, QUERY_TIMEOUT_MS);
  const state = { messageId: null, textParts: [], finished: false };
  const frameReader = new SseFrameReader();
  const decoder = new TextDecoder();
  return new Promise((resolve3) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      abortController?.signal.removeEventListener("abort", onAbort);
      resolve3(result);
    };
    async function cancelStream() {
      try {
        await streamReader.cancel();
      } catch {
      }
    }
    function settleNormal() {
      const fullText = state.textParts.join("").trim();
      if (!fullText && !state.errorMessage) {
        state.errorMessage = "Agent returned an empty response.";
      }
      logger.info("AG-UI run completed", {
        threadId: agThreadId,
        textLength: fullText.length,
        hasError: !!state.errorMessage
      });
      finish({ text: fullText, sessionId: agThreadId, error: state.errorMessage });
    }
    async function pump() {
      try {
        while (true) {
          if (abortController?.signal.aborted) {
            await cancelStream();
            settleNormal();
            return;
          }
          const { done, value } = await streamReader.read();
          if (done) {
            settleNormal();
            return;
          }
          const textChunk = decoder.decode(value, { stream: true });
          for (const ev of frameReader.push(textChunk)) {
            handleAgUiEvent(ev, state, {
              onText: (t) => {
                try {
                  onText?.(t);
                } catch {
                }
              },
              onTurnEnd: (r) => {
                try {
                  onTurnEnd?.(r);
                } catch {
                }
              }
            });
            if (state.finished) {
              await cancelStream();
              settleNormal();
              return;
            }
          }
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (!state.finished && !state.textParts.length && !state.errorMessage) {
          finish({ text: "", sessionId: agThreadId, error: `SSE stream error: ${msg}` });
        } else {
          settleNormal();
        }
      }
    }
    pump();
  });
}
var import_node_crypto2, SseFrameReader;
var init_provider = __esm({
  "electron/backend/wechat_bridge/src/claude/provider.ts"() {
    import_node_crypto2 = require("node:crypto");
    init_logger();
    SseFrameReader = class {
      buffer = "";
      push(chunk) {
        this.buffer += chunk;
        const events = [];
        const frames = this.buffer.split("\n\n");
        this.buffer = frames.pop() || "";
        for (const frame of frames) {
          const line = frame.trim();
          if (!line.startsWith("data:")) continue;
          const json = line.slice(5).trim();
          if (!json) continue;
          try {
            events.push(JSON.parse(json));
          } catch {
          }
        }
        return events;
      }
    };
  }
});

// electron/backend/wechat_bridge/src/claude/turn-router.ts
var TurnRouter;
var init_turn_router = __esm({
  "electron/backend/wechat_bridge/src/claude/turn-router.ts"() {
    TurnRouter = class {
      constructor(emit) {
        this.emit = emit;
      }
      turnBuffer = "";
      pendingFinal = "";
      onText(delta) {
        this.turnBuffer += delta;
      }
      onTurnEnd(stopReason) {
        const text = this.turnBuffer;
        this.turnBuffer = "";
        if (!text.trim()) return;
        if (stopReason === "tool_use") {
          this.emit({ text, role: "interstitial" });
        } else {
          this.pendingFinal += this.pendingFinal ? "\n\n" + text : text;
        }
      }
      /** 流结束时调用。先发 final，再 drain 残留 interstitial。 */
      drain() {
        if (this.pendingFinal.trim()) {
          this.emit({ text: this.pendingFinal, role: "final" });
          this.pendingFinal = "";
        }
        if (this.turnBuffer.trim()) {
          this.emit({ text: this.turnBuffer, role: "interstitial" });
          this.turnBuffer = "";
        }
      }
    };
  }
});

// electron/backend/wechat_bridge/src/claude/tool-noise-filter.ts
function isStructuralLine(line) {
  if (!line.trim()) return true;
  if (/^\s*```/.test(line)) return true;
  if (/^\s*\*+/.test(line)) return true;
  if (/^\s*[\[\]\{\}]/.test(line)) return true;
  if (/\\n/.test(line)) return true;
  if (/^\s*"\w+"\s*:/.test(line)) return true;
  if (/^\s*\*\s/.test(line)) return true;
  return false;
}
function extractTail(text) {
  const lines = text.split("\n");
  let lastStructuralIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isStructuralLine(lines[i])) lastStructuralIdx = i;
  }
  if (lastStructuralIdx < 0 || lastStructuralIdx === lines.length - 1) return "";
  return lines.slice(lastStructuralIdx + 1).join("\n").trim();
}
function filterToolNoise(text) {
  if (text.length <= LENGTH_THRESHOLD) return text;
  if (!FENCED_JSON.test(text)) return text;
  if (!URL_OR_PATH.test(text)) return text;
  const tail = extractTail(text);
  return tail ? `\u{1F527} [\u5DE5\u5177\u8C03\u7528] \u2014 ${tail}` : "\u{1F527} [\u5DE5\u5177\u8C03\u7528]";
}
var FENCED_JSON, URL_OR_PATH, LENGTH_THRESHOLD;
var init_tool_noise_filter = __esm({
  "electron/backend/wechat_bridge/src/claude/tool-noise-filter.ts"() {
    FENCED_JSON = /```json\b[\s\S]*?```/i;
    URL_OR_PATH = /(https?:\/\/\S+)|(\/(?:Users|home|tmp|var|opt|etc)\/\S+)|(~\/\S+)/;
    LENGTH_THRESHOLD = 400;
  }
});

// electron/backend/wechat_bridge/src/pending-queue.ts
function queuePath(accountId) {
  return (0, import_node_path13.join)(QUEUE_DIR, `${accountId}.json`);
}
function ensureDir() {
  if (!(0, import_node_fs11.existsSync)(QUEUE_DIR)) {
    (0, import_node_fs11.mkdirSync)(QUEUE_DIR, { recursive: true });
  }
}
function loadPendingQueue(accountId) {
  try {
    const path2 = queuePath(accountId);
    if (!(0, import_node_fs11.existsSync)(path2)) return [];
    const raw = (0, import_node_fs11.readFileSync)(path2, "utf-8");
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    logger.warn("Failed to load pending queue", {
      accountId,
      error: err instanceof Error ? err.message : String(err)
    });
    return [];
  }
}
function savePendingQueue(accountId, items) {
  try {
    ensureDir();
    (0, import_node_fs11.writeFileSync)(queuePath(accountId), JSON.stringify(items, null, 2), "utf-8");
  } catch (err) {
    logger.warn("Failed to save pending queue", {
      accountId,
      error: err instanceof Error ? err.message : String(err)
    });
  }
}
var import_node_fs11, import_node_path13, QUEUE_DIR;
var init_pending_queue = __esm({
  "electron/backend/wechat_bridge/src/pending-queue.ts"() {
    import_node_fs11 = require("node:fs");
    import_node_path13 = require("node:path");
    init_constants();
    init_logger();
    QUEUE_DIR = (0, import_node_path13.join)(DATA_DIR, "pending-queue");
  }
});

// node_modules/qrcode/lib/can-promise.js
var require_can_promise = __commonJS({
  "node_modules/qrcode/lib/can-promise.js"(exports2, module2) {
    module2.exports = function() {
      return typeof Promise === "function" && Promise.prototype && Promise.prototype.then;
    };
  }
});

// node_modules/qrcode/lib/core/utils.js
var require_utils = __commonJS({
  "node_modules/qrcode/lib/core/utils.js"(exports2) {
    var toSJISFunction;
    var CODEWORDS_COUNT = [
      0,
      // Not used
      26,
      44,
      70,
      100,
      134,
      172,
      196,
      242,
      292,
      346,
      404,
      466,
      532,
      581,
      655,
      733,
      815,
      901,
      991,
      1085,
      1156,
      1258,
      1364,
      1474,
      1588,
      1706,
      1828,
      1921,
      2051,
      2185,
      2323,
      2465,
      2611,
      2761,
      2876,
      3034,
      3196,
      3362,
      3532,
      3706
    ];
    exports2.getSymbolSize = function getSymbolSize(version) {
      if (!version) throw new Error('"version" cannot be null or undefined');
      if (version < 1 || version > 40) throw new Error('"version" should be in range from 1 to 40');
      return version * 4 + 17;
    };
    exports2.getSymbolTotalCodewords = function getSymbolTotalCodewords(version) {
      return CODEWORDS_COUNT[version];
    };
    exports2.getBCHDigit = function(data) {
      let digit = 0;
      while (data !== 0) {
        digit++;
        data >>>= 1;
      }
      return digit;
    };
    exports2.setToSJISFunction = function setToSJISFunction(f) {
      if (typeof f !== "function") {
        throw new Error('"toSJISFunc" is not a valid function.');
      }
      toSJISFunction = f;
    };
    exports2.isKanjiModeEnabled = function() {
      return typeof toSJISFunction !== "undefined";
    };
    exports2.toSJIS = function toSJIS(kanji) {
      return toSJISFunction(kanji);
    };
  }
});

// node_modules/qrcode/lib/core/error-correction-level.js
var require_error_correction_level = __commonJS({
  "node_modules/qrcode/lib/core/error-correction-level.js"(exports2) {
    exports2.L = { bit: 1 };
    exports2.M = { bit: 0 };
    exports2.Q = { bit: 3 };
    exports2.H = { bit: 2 };
    function fromString(string) {
      if (typeof string !== "string") {
        throw new Error("Param is not a string");
      }
      const lcStr = string.toLowerCase();
      switch (lcStr) {
        case "l":
        case "low":
          return exports2.L;
        case "m":
        case "medium":
          return exports2.M;
        case "q":
        case "quartile":
          return exports2.Q;
        case "h":
        case "high":
          return exports2.H;
        default:
          throw new Error("Unknown EC Level: " + string);
      }
    }
    exports2.isValid = function isValid(level) {
      return level && typeof level.bit !== "undefined" && level.bit >= 0 && level.bit < 4;
    };
    exports2.from = function from(value, defaultValue) {
      if (exports2.isValid(value)) {
        return value;
      }
      try {
        return fromString(value);
      } catch (e) {
        return defaultValue;
      }
    };
  }
});

// node_modules/qrcode/lib/core/bit-buffer.js
var require_bit_buffer = __commonJS({
  "node_modules/qrcode/lib/core/bit-buffer.js"(exports2, module2) {
    function BitBuffer() {
      this.buffer = [];
      this.length = 0;
    }
    BitBuffer.prototype = {
      get: function(index) {
        const bufIndex = Math.floor(index / 8);
        return (this.buffer[bufIndex] >>> 7 - index % 8 & 1) === 1;
      },
      put: function(num, length) {
        for (let i = 0; i < length; i++) {
          this.putBit((num >>> length - i - 1 & 1) === 1);
        }
      },
      getLengthInBits: function() {
        return this.length;
      },
      putBit: function(bit) {
        const bufIndex = Math.floor(this.length / 8);
        if (this.buffer.length <= bufIndex) {
          this.buffer.push(0);
        }
        if (bit) {
          this.buffer[bufIndex] |= 128 >>> this.length % 8;
        }
        this.length++;
      }
    };
    module2.exports = BitBuffer;
  }
});

// node_modules/qrcode/lib/core/bit-matrix.js
var require_bit_matrix = __commonJS({
  "node_modules/qrcode/lib/core/bit-matrix.js"(exports2, module2) {
    function BitMatrix(size) {
      if (!size || size < 1) {
        throw new Error("BitMatrix size must be defined and greater than 0");
      }
      this.size = size;
      this.data = new Uint8Array(size * size);
      this.reservedBit = new Uint8Array(size * size);
    }
    BitMatrix.prototype.set = function(row, col, value, reserved) {
      const index = row * this.size + col;
      this.data[index] = value;
      if (reserved) this.reservedBit[index] = true;
    };
    BitMatrix.prototype.get = function(row, col) {
      return this.data[row * this.size + col];
    };
    BitMatrix.prototype.xor = function(row, col, value) {
      this.data[row * this.size + col] ^= value;
    };
    BitMatrix.prototype.isReserved = function(row, col) {
      return this.reservedBit[row * this.size + col];
    };
    module2.exports = BitMatrix;
  }
});

// node_modules/qrcode/lib/core/alignment-pattern.js
var require_alignment_pattern = __commonJS({
  "node_modules/qrcode/lib/core/alignment-pattern.js"(exports2) {
    var getSymbolSize = require_utils().getSymbolSize;
    exports2.getRowColCoords = function getRowColCoords(version) {
      if (version === 1) return [];
      const posCount = Math.floor(version / 7) + 2;
      const size = getSymbolSize(version);
      const intervals = size === 145 ? 26 : Math.ceil((size - 13) / (2 * posCount - 2)) * 2;
      const positions = [size - 7];
      for (let i = 1; i < posCount - 1; i++) {
        positions[i] = positions[i - 1] - intervals;
      }
      positions.push(6);
      return positions.reverse();
    };
    exports2.getPositions = function getPositions(version) {
      const coords = [];
      const pos = exports2.getRowColCoords(version);
      const posLength = pos.length;
      for (let i = 0; i < posLength; i++) {
        for (let j = 0; j < posLength; j++) {
          if (i === 0 && j === 0 || // top-left
          i === 0 && j === posLength - 1 || // bottom-left
          i === posLength - 1 && j === 0) {
            continue;
          }
          coords.push([pos[i], pos[j]]);
        }
      }
      return coords;
    };
  }
});

// node_modules/qrcode/lib/core/finder-pattern.js
var require_finder_pattern = __commonJS({
  "node_modules/qrcode/lib/core/finder-pattern.js"(exports2) {
    var getSymbolSize = require_utils().getSymbolSize;
    var FINDER_PATTERN_SIZE = 7;
    exports2.getPositions = function getPositions(version) {
      const size = getSymbolSize(version);
      return [
        // top-left
        [0, 0],
        // top-right
        [size - FINDER_PATTERN_SIZE, 0],
        // bottom-left
        [0, size - FINDER_PATTERN_SIZE]
      ];
    };
  }
});

// node_modules/qrcode/lib/core/mask-pattern.js
var require_mask_pattern = __commonJS({
  "node_modules/qrcode/lib/core/mask-pattern.js"(exports2) {
    exports2.Patterns = {
      PATTERN000: 0,
      PATTERN001: 1,
      PATTERN010: 2,
      PATTERN011: 3,
      PATTERN100: 4,
      PATTERN101: 5,
      PATTERN110: 6,
      PATTERN111: 7
    };
    var PenaltyScores = {
      N1: 3,
      N2: 3,
      N3: 40,
      N4: 10
    };
    exports2.isValid = function isValid(mask) {
      return mask != null && mask !== "" && !isNaN(mask) && mask >= 0 && mask <= 7;
    };
    exports2.from = function from(value) {
      return exports2.isValid(value) ? parseInt(value, 10) : void 0;
    };
    exports2.getPenaltyN1 = function getPenaltyN1(data) {
      const size = data.size;
      let points = 0;
      let sameCountCol = 0;
      let sameCountRow = 0;
      let lastCol = null;
      let lastRow = null;
      for (let row = 0; row < size; row++) {
        sameCountCol = sameCountRow = 0;
        lastCol = lastRow = null;
        for (let col = 0; col < size; col++) {
          let module3 = data.get(row, col);
          if (module3 === lastCol) {
            sameCountCol++;
          } else {
            if (sameCountCol >= 5) points += PenaltyScores.N1 + (sameCountCol - 5);
            lastCol = module3;
            sameCountCol = 1;
          }
          module3 = data.get(col, row);
          if (module3 === lastRow) {
            sameCountRow++;
          } else {
            if (sameCountRow >= 5) points += PenaltyScores.N1 + (sameCountRow - 5);
            lastRow = module3;
            sameCountRow = 1;
          }
        }
        if (sameCountCol >= 5) points += PenaltyScores.N1 + (sameCountCol - 5);
        if (sameCountRow >= 5) points += PenaltyScores.N1 + (sameCountRow - 5);
      }
      return points;
    };
    exports2.getPenaltyN2 = function getPenaltyN2(data) {
      const size = data.size;
      let points = 0;
      for (let row = 0; row < size - 1; row++) {
        for (let col = 0; col < size - 1; col++) {
          const last = data.get(row, col) + data.get(row, col + 1) + data.get(row + 1, col) + data.get(row + 1, col + 1);
          if (last === 4 || last === 0) points++;
        }
      }
      return points * PenaltyScores.N2;
    };
    exports2.getPenaltyN3 = function getPenaltyN3(data) {
      const size = data.size;
      let points = 0;
      let bitsCol = 0;
      let bitsRow = 0;
      for (let row = 0; row < size; row++) {
        bitsCol = bitsRow = 0;
        for (let col = 0; col < size; col++) {
          bitsCol = bitsCol << 1 & 2047 | data.get(row, col);
          if (col >= 10 && (bitsCol === 1488 || bitsCol === 93)) points++;
          bitsRow = bitsRow << 1 & 2047 | data.get(col, row);
          if (col >= 10 && (bitsRow === 1488 || bitsRow === 93)) points++;
        }
      }
      return points * PenaltyScores.N3;
    };
    exports2.getPenaltyN4 = function getPenaltyN4(data) {
      let darkCount = 0;
      const modulesCount = data.data.length;
      for (let i = 0; i < modulesCount; i++) darkCount += data.data[i];
      const k = Math.abs(Math.ceil(darkCount * 100 / modulesCount / 5) - 10);
      return k * PenaltyScores.N4;
    };
    function getMaskAt(maskPattern, i, j) {
      switch (maskPattern) {
        case exports2.Patterns.PATTERN000:
          return (i + j) % 2 === 0;
        case exports2.Patterns.PATTERN001:
          return i % 2 === 0;
        case exports2.Patterns.PATTERN010:
          return j % 3 === 0;
        case exports2.Patterns.PATTERN011:
          return (i + j) % 3 === 0;
        case exports2.Patterns.PATTERN100:
          return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0;
        case exports2.Patterns.PATTERN101:
          return i * j % 2 + i * j % 3 === 0;
        case exports2.Patterns.PATTERN110:
          return (i * j % 2 + i * j % 3) % 2 === 0;
        case exports2.Patterns.PATTERN111:
          return (i * j % 3 + (i + j) % 2) % 2 === 0;
        default:
          throw new Error("bad maskPattern:" + maskPattern);
      }
    }
    exports2.applyMask = function applyMask(pattern, data) {
      const size = data.size;
      for (let col = 0; col < size; col++) {
        for (let row = 0; row < size; row++) {
          if (data.isReserved(row, col)) continue;
          data.xor(row, col, getMaskAt(pattern, row, col));
        }
      }
    };
    exports2.getBestMask = function getBestMask(data, setupFormatFunc) {
      const numPatterns = Object.keys(exports2.Patterns).length;
      let bestPattern = 0;
      let lowerPenalty = Infinity;
      for (let p = 0; p < numPatterns; p++) {
        setupFormatFunc(p);
        exports2.applyMask(p, data);
        const penalty = exports2.getPenaltyN1(data) + exports2.getPenaltyN2(data) + exports2.getPenaltyN3(data) + exports2.getPenaltyN4(data);
        exports2.applyMask(p, data);
        if (penalty < lowerPenalty) {
          lowerPenalty = penalty;
          bestPattern = p;
        }
      }
      return bestPattern;
    };
  }
});

// node_modules/qrcode/lib/core/error-correction-code.js
var require_error_correction_code = __commonJS({
  "node_modules/qrcode/lib/core/error-correction-code.js"(exports2) {
    var ECLevel = require_error_correction_level();
    var EC_BLOCKS_TABLE = [
      // L  M  Q  H
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      1,
      2,
      2,
      1,
      2,
      2,
      4,
      1,
      2,
      4,
      4,
      2,
      4,
      4,
      4,
      2,
      4,
      6,
      5,
      2,
      4,
      6,
      6,
      2,
      5,
      8,
      8,
      4,
      5,
      8,
      8,
      4,
      5,
      8,
      11,
      4,
      8,
      10,
      11,
      4,
      9,
      12,
      16,
      4,
      9,
      16,
      16,
      6,
      10,
      12,
      18,
      6,
      10,
      17,
      16,
      6,
      11,
      16,
      19,
      6,
      13,
      18,
      21,
      7,
      14,
      21,
      25,
      8,
      16,
      20,
      25,
      8,
      17,
      23,
      25,
      9,
      17,
      23,
      34,
      9,
      18,
      25,
      30,
      10,
      20,
      27,
      32,
      12,
      21,
      29,
      35,
      12,
      23,
      34,
      37,
      12,
      25,
      34,
      40,
      13,
      26,
      35,
      42,
      14,
      28,
      38,
      45,
      15,
      29,
      40,
      48,
      16,
      31,
      43,
      51,
      17,
      33,
      45,
      54,
      18,
      35,
      48,
      57,
      19,
      37,
      51,
      60,
      19,
      38,
      53,
      63,
      20,
      40,
      56,
      66,
      21,
      43,
      59,
      70,
      22,
      45,
      62,
      74,
      24,
      47,
      65,
      77,
      25,
      49,
      68,
      81
    ];
    var EC_CODEWORDS_TABLE = [
      // L  M  Q  H
      7,
      10,
      13,
      17,
      10,
      16,
      22,
      28,
      15,
      26,
      36,
      44,
      20,
      36,
      52,
      64,
      26,
      48,
      72,
      88,
      36,
      64,
      96,
      112,
      40,
      72,
      108,
      130,
      48,
      88,
      132,
      156,
      60,
      110,
      160,
      192,
      72,
      130,
      192,
      224,
      80,
      150,
      224,
      264,
      96,
      176,
      260,
      308,
      104,
      198,
      288,
      352,
      120,
      216,
      320,
      384,
      132,
      240,
      360,
      432,
      144,
      280,
      408,
      480,
      168,
      308,
      448,
      532,
      180,
      338,
      504,
      588,
      196,
      364,
      546,
      650,
      224,
      416,
      600,
      700,
      224,
      442,
      644,
      750,
      252,
      476,
      690,
      816,
      270,
      504,
      750,
      900,
      300,
      560,
      810,
      960,
      312,
      588,
      870,
      1050,
      336,
      644,
      952,
      1110,
      360,
      700,
      1020,
      1200,
      390,
      728,
      1050,
      1260,
      420,
      784,
      1140,
      1350,
      450,
      812,
      1200,
      1440,
      480,
      868,
      1290,
      1530,
      510,
      924,
      1350,
      1620,
      540,
      980,
      1440,
      1710,
      570,
      1036,
      1530,
      1800,
      570,
      1064,
      1590,
      1890,
      600,
      1120,
      1680,
      1980,
      630,
      1204,
      1770,
      2100,
      660,
      1260,
      1860,
      2220,
      720,
      1316,
      1950,
      2310,
      750,
      1372,
      2040,
      2430
    ];
    exports2.getBlocksCount = function getBlocksCount(version, errorCorrectionLevel) {
      switch (errorCorrectionLevel) {
        case ECLevel.L:
          return EC_BLOCKS_TABLE[(version - 1) * 4 + 0];
        case ECLevel.M:
          return EC_BLOCKS_TABLE[(version - 1) * 4 + 1];
        case ECLevel.Q:
          return EC_BLOCKS_TABLE[(version - 1) * 4 + 2];
        case ECLevel.H:
          return EC_BLOCKS_TABLE[(version - 1) * 4 + 3];
        default:
          return void 0;
      }
    };
    exports2.getTotalCodewordsCount = function getTotalCodewordsCount(version, errorCorrectionLevel) {
      switch (errorCorrectionLevel) {
        case ECLevel.L:
          return EC_CODEWORDS_TABLE[(version - 1) * 4 + 0];
        case ECLevel.M:
          return EC_CODEWORDS_TABLE[(version - 1) * 4 + 1];
        case ECLevel.Q:
          return EC_CODEWORDS_TABLE[(version - 1) * 4 + 2];
        case ECLevel.H:
          return EC_CODEWORDS_TABLE[(version - 1) * 4 + 3];
        default:
          return void 0;
      }
    };
  }
});

// node_modules/qrcode/lib/core/galois-field.js
var require_galois_field = __commonJS({
  "node_modules/qrcode/lib/core/galois-field.js"(exports2) {
    var EXP_TABLE = new Uint8Array(512);
    var LOG_TABLE = new Uint8Array(256);
    (function initTables() {
      let x = 1;
      for (let i = 0; i < 255; i++) {
        EXP_TABLE[i] = x;
        LOG_TABLE[x] = i;
        x <<= 1;
        if (x & 256) {
          x ^= 285;
        }
      }
      for (let i = 255; i < 512; i++) {
        EXP_TABLE[i] = EXP_TABLE[i - 255];
      }
    })();
    exports2.log = function log(n) {
      if (n < 1) throw new Error("log(" + n + ")");
      return LOG_TABLE[n];
    };
    exports2.exp = function exp(n) {
      return EXP_TABLE[n];
    };
    exports2.mul = function mul(x, y) {
      if (x === 0 || y === 0) return 0;
      return EXP_TABLE[LOG_TABLE[x] + LOG_TABLE[y]];
    };
  }
});

// node_modules/qrcode/lib/core/polynomial.js
var require_polynomial = __commonJS({
  "node_modules/qrcode/lib/core/polynomial.js"(exports2) {
    var GF = require_galois_field();
    exports2.mul = function mul(p1, p2) {
      const coeff = new Uint8Array(p1.length + p2.length - 1);
      for (let i = 0; i < p1.length; i++) {
        for (let j = 0; j < p2.length; j++) {
          coeff[i + j] ^= GF.mul(p1[i], p2[j]);
        }
      }
      return coeff;
    };
    exports2.mod = function mod(divident, divisor) {
      let result = new Uint8Array(divident);
      while (result.length - divisor.length >= 0) {
        const coeff = result[0];
        for (let i = 0; i < divisor.length; i++) {
          result[i] ^= GF.mul(divisor[i], coeff);
        }
        let offset = 0;
        while (offset < result.length && result[offset] === 0) offset++;
        result = result.slice(offset);
      }
      return result;
    };
    exports2.generateECPolynomial = function generateECPolynomial(degree) {
      let poly = new Uint8Array([1]);
      for (let i = 0; i < degree; i++) {
        poly = exports2.mul(poly, new Uint8Array([1, GF.exp(i)]));
      }
      return poly;
    };
  }
});

// node_modules/qrcode/lib/core/reed-solomon-encoder.js
var require_reed_solomon_encoder = __commonJS({
  "node_modules/qrcode/lib/core/reed-solomon-encoder.js"(exports2, module2) {
    var Polynomial = require_polynomial();
    function ReedSolomonEncoder(degree) {
      this.genPoly = void 0;
      this.degree = degree;
      if (this.degree) this.initialize(this.degree);
    }
    ReedSolomonEncoder.prototype.initialize = function initialize(degree) {
      this.degree = degree;
      this.genPoly = Polynomial.generateECPolynomial(this.degree);
    };
    ReedSolomonEncoder.prototype.encode = function encode(data) {
      if (!this.genPoly) {
        throw new Error("Encoder not initialized");
      }
      const paddedData = new Uint8Array(data.length + this.degree);
      paddedData.set(data);
      const remainder = Polynomial.mod(paddedData, this.genPoly);
      const start = this.degree - remainder.length;
      if (start > 0) {
        const buff = new Uint8Array(this.degree);
        buff.set(remainder, start);
        return buff;
      }
      return remainder;
    };
    module2.exports = ReedSolomonEncoder;
  }
});

// node_modules/qrcode/lib/core/version-check.js
var require_version_check = __commonJS({
  "node_modules/qrcode/lib/core/version-check.js"(exports2) {
    exports2.isValid = function isValid(version) {
      return !isNaN(version) && version >= 1 && version <= 40;
    };
  }
});

// node_modules/qrcode/lib/core/regex.js
var require_regex = __commonJS({
  "node_modules/qrcode/lib/core/regex.js"(exports2) {
    var numeric = "[0-9]+";
    var alphanumeric = "[A-Z $%*+\\-./:]+";
    var kanji = "(?:[u3000-u303F]|[u3040-u309F]|[u30A0-u30FF]|[uFF00-uFFEF]|[u4E00-u9FAF]|[u2605-u2606]|[u2190-u2195]|u203B|[u2010u2015u2018u2019u2025u2026u201Cu201Du2225u2260]|[u0391-u0451]|[u00A7u00A8u00B1u00B4u00D7u00F7])+";
    kanji = kanji.replace(/u/g, "\\u");
    var byte = "(?:(?![A-Z0-9 $%*+\\-./:]|" + kanji + ")(?:.|[\r\n]))+";
    exports2.KANJI = new RegExp(kanji, "g");
    exports2.BYTE_KANJI = new RegExp("[^A-Z0-9 $%*+\\-./:]+", "g");
    exports2.BYTE = new RegExp(byte, "g");
    exports2.NUMERIC = new RegExp(numeric, "g");
    exports2.ALPHANUMERIC = new RegExp(alphanumeric, "g");
    var TEST_KANJI = new RegExp("^" + kanji + "$");
    var TEST_NUMERIC = new RegExp("^" + numeric + "$");
    var TEST_ALPHANUMERIC = new RegExp("^[A-Z0-9 $%*+\\-./:]+$");
    exports2.testKanji = function testKanji(str) {
      return TEST_KANJI.test(str);
    };
    exports2.testNumeric = function testNumeric(str) {
      return TEST_NUMERIC.test(str);
    };
    exports2.testAlphanumeric = function testAlphanumeric(str) {
      return TEST_ALPHANUMERIC.test(str);
    };
  }
});

// node_modules/qrcode/lib/core/mode.js
var require_mode = __commonJS({
  "node_modules/qrcode/lib/core/mode.js"(exports2) {
    var VersionCheck = require_version_check();
    var Regex = require_regex();
    exports2.NUMERIC = {
      id: "Numeric",
      bit: 1 << 0,
      ccBits: [10, 12, 14]
    };
    exports2.ALPHANUMERIC = {
      id: "Alphanumeric",
      bit: 1 << 1,
      ccBits: [9, 11, 13]
    };
    exports2.BYTE = {
      id: "Byte",
      bit: 1 << 2,
      ccBits: [8, 16, 16]
    };
    exports2.KANJI = {
      id: "Kanji",
      bit: 1 << 3,
      ccBits: [8, 10, 12]
    };
    exports2.MIXED = {
      bit: -1
    };
    exports2.getCharCountIndicator = function getCharCountIndicator(mode, version) {
      if (!mode.ccBits) throw new Error("Invalid mode: " + mode);
      if (!VersionCheck.isValid(version)) {
        throw new Error("Invalid version: " + version);
      }
      if (version >= 1 && version < 10) return mode.ccBits[0];
      else if (version < 27) return mode.ccBits[1];
      return mode.ccBits[2];
    };
    exports2.getBestModeForData = function getBestModeForData(dataStr) {
      if (Regex.testNumeric(dataStr)) return exports2.NUMERIC;
      else if (Regex.testAlphanumeric(dataStr)) return exports2.ALPHANUMERIC;
      else if (Regex.testKanji(dataStr)) return exports2.KANJI;
      else return exports2.BYTE;
    };
    exports2.toString = function toString(mode) {
      if (mode && mode.id) return mode.id;
      throw new Error("Invalid mode");
    };
    exports2.isValid = function isValid(mode) {
      return mode && mode.bit && mode.ccBits;
    };
    function fromString(string) {
      if (typeof string !== "string") {
        throw new Error("Param is not a string");
      }
      const lcStr = string.toLowerCase();
      switch (lcStr) {
        case "numeric":
          return exports2.NUMERIC;
        case "alphanumeric":
          return exports2.ALPHANUMERIC;
        case "kanji":
          return exports2.KANJI;
        case "byte":
          return exports2.BYTE;
        default:
          throw new Error("Unknown mode: " + string);
      }
    }
    exports2.from = function from(value, defaultValue) {
      if (exports2.isValid(value)) {
        return value;
      }
      try {
        return fromString(value);
      } catch (e) {
        return defaultValue;
      }
    };
  }
});

// node_modules/qrcode/lib/core/version.js
var require_version = __commonJS({
  "node_modules/qrcode/lib/core/version.js"(exports2) {
    var Utils = require_utils();
    var ECCode = require_error_correction_code();
    var ECLevel = require_error_correction_level();
    var Mode = require_mode();
    var VersionCheck = require_version_check();
    var G18 = 1 << 12 | 1 << 11 | 1 << 10 | 1 << 9 | 1 << 8 | 1 << 5 | 1 << 2 | 1 << 0;
    var G18_BCH = Utils.getBCHDigit(G18);
    function getBestVersionForDataLength(mode, length, errorCorrectionLevel) {
      for (let currentVersion = 1; currentVersion <= 40; currentVersion++) {
        if (length <= exports2.getCapacity(currentVersion, errorCorrectionLevel, mode)) {
          return currentVersion;
        }
      }
      return void 0;
    }
    function getReservedBitsCount(mode, version) {
      return Mode.getCharCountIndicator(mode, version) + 4;
    }
    function getTotalBitsFromDataArray(segments, version) {
      let totalBits = 0;
      segments.forEach(function(data) {
        const reservedBits = getReservedBitsCount(data.mode, version);
        totalBits += reservedBits + data.getBitsLength();
      });
      return totalBits;
    }
    function getBestVersionForMixedData(segments, errorCorrectionLevel) {
      for (let currentVersion = 1; currentVersion <= 40; currentVersion++) {
        const length = getTotalBitsFromDataArray(segments, currentVersion);
        if (length <= exports2.getCapacity(currentVersion, errorCorrectionLevel, Mode.MIXED)) {
          return currentVersion;
        }
      }
      return void 0;
    }
    exports2.from = function from(value, defaultValue) {
      if (VersionCheck.isValid(value)) {
        return parseInt(value, 10);
      }
      return defaultValue;
    };
    exports2.getCapacity = function getCapacity(version, errorCorrectionLevel, mode) {
      if (!VersionCheck.isValid(version)) {
        throw new Error("Invalid QR Code version");
      }
      if (typeof mode === "undefined") mode = Mode.BYTE;
      const totalCodewords = Utils.getSymbolTotalCodewords(version);
      const ecTotalCodewords = ECCode.getTotalCodewordsCount(version, errorCorrectionLevel);
      const dataTotalCodewordsBits = (totalCodewords - ecTotalCodewords) * 8;
      if (mode === Mode.MIXED) return dataTotalCodewordsBits;
      const usableBits = dataTotalCodewordsBits - getReservedBitsCount(mode, version);
      switch (mode) {
        case Mode.NUMERIC:
          return Math.floor(usableBits / 10 * 3);
        case Mode.ALPHANUMERIC:
          return Math.floor(usableBits / 11 * 2);
        case Mode.KANJI:
          return Math.floor(usableBits / 13);
        case Mode.BYTE:
        default:
          return Math.floor(usableBits / 8);
      }
    };
    exports2.getBestVersionForData = function getBestVersionForData(data, errorCorrectionLevel) {
      let seg;
      const ecl = ECLevel.from(errorCorrectionLevel, ECLevel.M);
      if (Array.isArray(data)) {
        if (data.length > 1) {
          return getBestVersionForMixedData(data, ecl);
        }
        if (data.length === 0) {
          return 1;
        }
        seg = data[0];
      } else {
        seg = data;
      }
      return getBestVersionForDataLength(seg.mode, seg.getLength(), ecl);
    };
    exports2.getEncodedBits = function getEncodedBits(version) {
      if (!VersionCheck.isValid(version) || version < 7) {
        throw new Error("Invalid QR Code version");
      }
      let d = version << 12;
      while (Utils.getBCHDigit(d) - G18_BCH >= 0) {
        d ^= G18 << Utils.getBCHDigit(d) - G18_BCH;
      }
      return version << 12 | d;
    };
  }
});

// node_modules/qrcode/lib/core/format-info.js
var require_format_info = __commonJS({
  "node_modules/qrcode/lib/core/format-info.js"(exports2) {
    var Utils = require_utils();
    var G15 = 1 << 10 | 1 << 8 | 1 << 5 | 1 << 4 | 1 << 2 | 1 << 1 | 1 << 0;
    var G15_MASK = 1 << 14 | 1 << 12 | 1 << 10 | 1 << 4 | 1 << 1;
    var G15_BCH = Utils.getBCHDigit(G15);
    exports2.getEncodedBits = function getEncodedBits(errorCorrectionLevel, mask) {
      const data = errorCorrectionLevel.bit << 3 | mask;
      let d = data << 10;
      while (Utils.getBCHDigit(d) - G15_BCH >= 0) {
        d ^= G15 << Utils.getBCHDigit(d) - G15_BCH;
      }
      return (data << 10 | d) ^ G15_MASK;
    };
  }
});

// node_modules/qrcode/lib/core/numeric-data.js
var require_numeric_data = __commonJS({
  "node_modules/qrcode/lib/core/numeric-data.js"(exports2, module2) {
    var Mode = require_mode();
    function NumericData(data) {
      this.mode = Mode.NUMERIC;
      this.data = data.toString();
    }
    NumericData.getBitsLength = function getBitsLength(length) {
      return 10 * Math.floor(length / 3) + (length % 3 ? length % 3 * 3 + 1 : 0);
    };
    NumericData.prototype.getLength = function getLength() {
      return this.data.length;
    };
    NumericData.prototype.getBitsLength = function getBitsLength() {
      return NumericData.getBitsLength(this.data.length);
    };
    NumericData.prototype.write = function write(bitBuffer) {
      let i, group, value;
      for (i = 0; i + 3 <= this.data.length; i += 3) {
        group = this.data.substr(i, 3);
        value = parseInt(group, 10);
        bitBuffer.put(value, 10);
      }
      const remainingNum = this.data.length - i;
      if (remainingNum > 0) {
        group = this.data.substr(i);
        value = parseInt(group, 10);
        bitBuffer.put(value, remainingNum * 3 + 1);
      }
    };
    module2.exports = NumericData;
  }
});

// node_modules/qrcode/lib/core/alphanumeric-data.js
var require_alphanumeric_data = __commonJS({
  "node_modules/qrcode/lib/core/alphanumeric-data.js"(exports2, module2) {
    var Mode = require_mode();
    var ALPHA_NUM_CHARS = [
      "0",
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9",
      "A",
      "B",
      "C",
      "D",
      "E",
      "F",
      "G",
      "H",
      "I",
      "J",
      "K",
      "L",
      "M",
      "N",
      "O",
      "P",
      "Q",
      "R",
      "S",
      "T",
      "U",
      "V",
      "W",
      "X",
      "Y",
      "Z",
      " ",
      "$",
      "%",
      "*",
      "+",
      "-",
      ".",
      "/",
      ":"
    ];
    function AlphanumericData(data) {
      this.mode = Mode.ALPHANUMERIC;
      this.data = data;
    }
    AlphanumericData.getBitsLength = function getBitsLength(length) {
      return 11 * Math.floor(length / 2) + 6 * (length % 2);
    };
    AlphanumericData.prototype.getLength = function getLength() {
      return this.data.length;
    };
    AlphanumericData.prototype.getBitsLength = function getBitsLength() {
      return AlphanumericData.getBitsLength(this.data.length);
    };
    AlphanumericData.prototype.write = function write(bitBuffer) {
      let i;
      for (i = 0; i + 2 <= this.data.length; i += 2) {
        let value = ALPHA_NUM_CHARS.indexOf(this.data[i]) * 45;
        value += ALPHA_NUM_CHARS.indexOf(this.data[i + 1]);
        bitBuffer.put(value, 11);
      }
      if (this.data.length % 2) {
        bitBuffer.put(ALPHA_NUM_CHARS.indexOf(this.data[i]), 6);
      }
    };
    module2.exports = AlphanumericData;
  }
});

// node_modules/qrcode/lib/core/byte-data.js
var require_byte_data = __commonJS({
  "node_modules/qrcode/lib/core/byte-data.js"(exports2, module2) {
    var Mode = require_mode();
    function ByteData(data) {
      this.mode = Mode.BYTE;
      if (typeof data === "string") {
        this.data = new TextEncoder().encode(data);
      } else {
        this.data = new Uint8Array(data);
      }
    }
    ByteData.getBitsLength = function getBitsLength(length) {
      return length * 8;
    };
    ByteData.prototype.getLength = function getLength() {
      return this.data.length;
    };
    ByteData.prototype.getBitsLength = function getBitsLength() {
      return ByteData.getBitsLength(this.data.length);
    };
    ByteData.prototype.write = function(bitBuffer) {
      for (let i = 0, l = this.data.length; i < l; i++) {
        bitBuffer.put(this.data[i], 8);
      }
    };
    module2.exports = ByteData;
  }
});

// node_modules/qrcode/lib/core/kanji-data.js
var require_kanji_data = __commonJS({
  "node_modules/qrcode/lib/core/kanji-data.js"(exports2, module2) {
    var Mode = require_mode();
    var Utils = require_utils();
    function KanjiData(data) {
      this.mode = Mode.KANJI;
      this.data = data;
    }
    KanjiData.getBitsLength = function getBitsLength(length) {
      return length * 13;
    };
    KanjiData.prototype.getLength = function getLength() {
      return this.data.length;
    };
    KanjiData.prototype.getBitsLength = function getBitsLength() {
      return KanjiData.getBitsLength(this.data.length);
    };
    KanjiData.prototype.write = function(bitBuffer) {
      let i;
      for (i = 0; i < this.data.length; i++) {
        let value = Utils.toSJIS(this.data[i]);
        if (value >= 33088 && value <= 40956) {
          value -= 33088;
        } else if (value >= 57408 && value <= 60351) {
          value -= 49472;
        } else {
          throw new Error(
            "Invalid SJIS character: " + this.data[i] + "\nMake sure your charset is UTF-8"
          );
        }
        value = (value >>> 8 & 255) * 192 + (value & 255);
        bitBuffer.put(value, 13);
      }
    };
    module2.exports = KanjiData;
  }
});

// node_modules/dijkstrajs/dijkstra.js
var require_dijkstra = __commonJS({
  "node_modules/dijkstrajs/dijkstra.js"(exports2, module2) {
    "use strict";
    var dijkstra = {
      single_source_shortest_paths: function(graph, s, d) {
        var predecessors = {};
        var costs = {};
        costs[s] = 0;
        var open = dijkstra.PriorityQueue.make();
        open.push(s, 0);
        var closest, u, v, cost_of_s_to_u, adjacent_nodes, cost_of_e, cost_of_s_to_u_plus_cost_of_e, cost_of_s_to_v, first_visit;
        while (!open.empty()) {
          closest = open.pop();
          u = closest.value;
          cost_of_s_to_u = closest.cost;
          adjacent_nodes = graph[u] || {};
          for (v in adjacent_nodes) {
            if (adjacent_nodes.hasOwnProperty(v)) {
              cost_of_e = adjacent_nodes[v];
              cost_of_s_to_u_plus_cost_of_e = cost_of_s_to_u + cost_of_e;
              cost_of_s_to_v = costs[v];
              first_visit = typeof costs[v] === "undefined";
              if (first_visit || cost_of_s_to_v > cost_of_s_to_u_plus_cost_of_e) {
                costs[v] = cost_of_s_to_u_plus_cost_of_e;
                open.push(v, cost_of_s_to_u_plus_cost_of_e);
                predecessors[v] = u;
              }
            }
          }
        }
        if (typeof d !== "undefined" && typeof costs[d] === "undefined") {
          var msg = ["Could not find a path from ", s, " to ", d, "."].join("");
          throw new Error(msg);
        }
        return predecessors;
      },
      extract_shortest_path_from_predecessor_list: function(predecessors, d) {
        var nodes = [];
        var u = d;
        var predecessor;
        while (u) {
          nodes.push(u);
          predecessor = predecessors[u];
          u = predecessors[u];
        }
        nodes.reverse();
        return nodes;
      },
      find_path: function(graph, s, d) {
        var predecessors = dijkstra.single_source_shortest_paths(graph, s, d);
        return dijkstra.extract_shortest_path_from_predecessor_list(
          predecessors,
          d
        );
      },
      /**
       * A very naive priority queue implementation.
       */
      PriorityQueue: {
        make: function(opts) {
          var T = dijkstra.PriorityQueue, t = {}, key;
          opts = opts || {};
          for (key in T) {
            if (T.hasOwnProperty(key)) {
              t[key] = T[key];
            }
          }
          t.queue = [];
          t.sorter = opts.sorter || T.default_sorter;
          return t;
        },
        default_sorter: function(a, b) {
          return a.cost - b.cost;
        },
        /**
         * Add a new item to the queue and ensure the highest priority element
         * is at the front of the queue.
         */
        push: function(value, cost) {
          var item = { value, cost };
          this.queue.push(item);
          this.queue.sort(this.sorter);
        },
        /**
         * Return the highest priority element in the queue.
         */
        pop: function() {
          return this.queue.shift();
        },
        empty: function() {
          return this.queue.length === 0;
        }
      }
    };
    if (typeof module2 !== "undefined") {
      module2.exports = dijkstra;
    }
  }
});

// node_modules/qrcode/lib/core/segments.js
var require_segments = __commonJS({
  "node_modules/qrcode/lib/core/segments.js"(exports2) {
    var Mode = require_mode();
    var NumericData = require_numeric_data();
    var AlphanumericData = require_alphanumeric_data();
    var ByteData = require_byte_data();
    var KanjiData = require_kanji_data();
    var Regex = require_regex();
    var Utils = require_utils();
    var dijkstra = require_dijkstra();
    function getStringByteLength(str) {
      return unescape(encodeURIComponent(str)).length;
    }
    function getSegments(regex, mode, str) {
      const segments = [];
      let result;
      while ((result = regex.exec(str)) !== null) {
        segments.push({
          data: result[0],
          index: result.index,
          mode,
          length: result[0].length
        });
      }
      return segments;
    }
    function getSegmentsFromString(dataStr) {
      const numSegs = getSegments(Regex.NUMERIC, Mode.NUMERIC, dataStr);
      const alphaNumSegs = getSegments(Regex.ALPHANUMERIC, Mode.ALPHANUMERIC, dataStr);
      let byteSegs;
      let kanjiSegs;
      if (Utils.isKanjiModeEnabled()) {
        byteSegs = getSegments(Regex.BYTE, Mode.BYTE, dataStr);
        kanjiSegs = getSegments(Regex.KANJI, Mode.KANJI, dataStr);
      } else {
        byteSegs = getSegments(Regex.BYTE_KANJI, Mode.BYTE, dataStr);
        kanjiSegs = [];
      }
      const segs = numSegs.concat(alphaNumSegs, byteSegs, kanjiSegs);
      return segs.sort(function(s1, s2) {
        return s1.index - s2.index;
      }).map(function(obj) {
        return {
          data: obj.data,
          mode: obj.mode,
          length: obj.length
        };
      });
    }
    function getSegmentBitsLength(length, mode) {
      switch (mode) {
        case Mode.NUMERIC:
          return NumericData.getBitsLength(length);
        case Mode.ALPHANUMERIC:
          return AlphanumericData.getBitsLength(length);
        case Mode.KANJI:
          return KanjiData.getBitsLength(length);
        case Mode.BYTE:
          return ByteData.getBitsLength(length);
      }
    }
    function mergeSegments(segs) {
      return segs.reduce(function(acc, curr) {
        const prevSeg = acc.length - 1 >= 0 ? acc[acc.length - 1] : null;
        if (prevSeg && prevSeg.mode === curr.mode) {
          acc[acc.length - 1].data += curr.data;
          return acc;
        }
        acc.push(curr);
        return acc;
      }, []);
    }
    function buildNodes(segs) {
      const nodes = [];
      for (let i = 0; i < segs.length; i++) {
        const seg = segs[i];
        switch (seg.mode) {
          case Mode.NUMERIC:
            nodes.push([
              seg,
              { data: seg.data, mode: Mode.ALPHANUMERIC, length: seg.length },
              { data: seg.data, mode: Mode.BYTE, length: seg.length }
            ]);
            break;
          case Mode.ALPHANUMERIC:
            nodes.push([
              seg,
              { data: seg.data, mode: Mode.BYTE, length: seg.length }
            ]);
            break;
          case Mode.KANJI:
            nodes.push([
              seg,
              { data: seg.data, mode: Mode.BYTE, length: getStringByteLength(seg.data) }
            ]);
            break;
          case Mode.BYTE:
            nodes.push([
              { data: seg.data, mode: Mode.BYTE, length: getStringByteLength(seg.data) }
            ]);
        }
      }
      return nodes;
    }
    function buildGraph(nodes, version) {
      const table = {};
      const graph = { start: {} };
      let prevNodeIds = ["start"];
      for (let i = 0; i < nodes.length; i++) {
        const nodeGroup = nodes[i];
        const currentNodeIds = [];
        for (let j = 0; j < nodeGroup.length; j++) {
          const node = nodeGroup[j];
          const key = "" + i + j;
          currentNodeIds.push(key);
          table[key] = { node, lastCount: 0 };
          graph[key] = {};
          for (let n = 0; n < prevNodeIds.length; n++) {
            const prevNodeId = prevNodeIds[n];
            if (table[prevNodeId] && table[prevNodeId].node.mode === node.mode) {
              graph[prevNodeId][key] = getSegmentBitsLength(table[prevNodeId].lastCount + node.length, node.mode) - getSegmentBitsLength(table[prevNodeId].lastCount, node.mode);
              table[prevNodeId].lastCount += node.length;
            } else {
              if (table[prevNodeId]) table[prevNodeId].lastCount = node.length;
              graph[prevNodeId][key] = getSegmentBitsLength(node.length, node.mode) + 4 + Mode.getCharCountIndicator(node.mode, version);
            }
          }
        }
        prevNodeIds = currentNodeIds;
      }
      for (let n = 0; n < prevNodeIds.length; n++) {
        graph[prevNodeIds[n]].end = 0;
      }
      return { map: graph, table };
    }
    function buildSingleSegment(data, modesHint) {
      let mode;
      const bestMode = Mode.getBestModeForData(data);
      mode = Mode.from(modesHint, bestMode);
      if (mode !== Mode.BYTE && mode.bit < bestMode.bit) {
        throw new Error('"' + data + '" cannot be encoded with mode ' + Mode.toString(mode) + ".\n Suggested mode is: " + Mode.toString(bestMode));
      }
      if (mode === Mode.KANJI && !Utils.isKanjiModeEnabled()) {
        mode = Mode.BYTE;
      }
      switch (mode) {
        case Mode.NUMERIC:
          return new NumericData(data);
        case Mode.ALPHANUMERIC:
          return new AlphanumericData(data);
        case Mode.KANJI:
          return new KanjiData(data);
        case Mode.BYTE:
          return new ByteData(data);
      }
    }
    exports2.fromArray = function fromArray(array) {
      return array.reduce(function(acc, seg) {
        if (typeof seg === "string") {
          acc.push(buildSingleSegment(seg, null));
        } else if (seg.data) {
          acc.push(buildSingleSegment(seg.data, seg.mode));
        }
        return acc;
      }, []);
    };
    exports2.fromString = function fromString(data, version) {
      const segs = getSegmentsFromString(data, Utils.isKanjiModeEnabled());
      const nodes = buildNodes(segs);
      const graph = buildGraph(nodes, version);
      const path2 = dijkstra.find_path(graph.map, "start", "end");
      const optimizedSegs = [];
      for (let i = 1; i < path2.length - 1; i++) {
        optimizedSegs.push(graph.table[path2[i]].node);
      }
      return exports2.fromArray(mergeSegments(optimizedSegs));
    };
    exports2.rawSplit = function rawSplit(data) {
      return exports2.fromArray(
        getSegmentsFromString(data, Utils.isKanjiModeEnabled())
      );
    };
  }
});

// node_modules/qrcode/lib/core/qrcode.js
var require_qrcode = __commonJS({
  "node_modules/qrcode/lib/core/qrcode.js"(exports2) {
    var Utils = require_utils();
    var ECLevel = require_error_correction_level();
    var BitBuffer = require_bit_buffer();
    var BitMatrix = require_bit_matrix();
    var AlignmentPattern = require_alignment_pattern();
    var FinderPattern = require_finder_pattern();
    var MaskPattern = require_mask_pattern();
    var ECCode = require_error_correction_code();
    var ReedSolomonEncoder = require_reed_solomon_encoder();
    var Version = require_version();
    var FormatInfo = require_format_info();
    var Mode = require_mode();
    var Segments = require_segments();
    function setupFinderPattern(matrix, version) {
      const size = matrix.size;
      const pos = FinderPattern.getPositions(version);
      for (let i = 0; i < pos.length; i++) {
        const row = pos[i][0];
        const col = pos[i][1];
        for (let r = -1; r <= 7; r++) {
          if (row + r <= -1 || size <= row + r) continue;
          for (let c = -1; c <= 7; c++) {
            if (col + c <= -1 || size <= col + c) continue;
            if (r >= 0 && r <= 6 && (c === 0 || c === 6) || c >= 0 && c <= 6 && (r === 0 || r === 6) || r >= 2 && r <= 4 && c >= 2 && c <= 4) {
              matrix.set(row + r, col + c, true, true);
            } else {
              matrix.set(row + r, col + c, false, true);
            }
          }
        }
      }
    }
    function setupTimingPattern(matrix) {
      const size = matrix.size;
      for (let r = 8; r < size - 8; r++) {
        const value = r % 2 === 0;
        matrix.set(r, 6, value, true);
        matrix.set(6, r, value, true);
      }
    }
    function setupAlignmentPattern(matrix, version) {
      const pos = AlignmentPattern.getPositions(version);
      for (let i = 0; i < pos.length; i++) {
        const row = pos[i][0];
        const col = pos[i][1];
        for (let r = -2; r <= 2; r++) {
          for (let c = -2; c <= 2; c++) {
            if (r === -2 || r === 2 || c === -2 || c === 2 || r === 0 && c === 0) {
              matrix.set(row + r, col + c, true, true);
            } else {
              matrix.set(row + r, col + c, false, true);
            }
          }
        }
      }
    }
    function setupVersionInfo(matrix, version) {
      const size = matrix.size;
      const bits = Version.getEncodedBits(version);
      let row, col, mod;
      for (let i = 0; i < 18; i++) {
        row = Math.floor(i / 3);
        col = i % 3 + size - 8 - 3;
        mod = (bits >> i & 1) === 1;
        matrix.set(row, col, mod, true);
        matrix.set(col, row, mod, true);
      }
    }
    function setupFormatInfo(matrix, errorCorrectionLevel, maskPattern) {
      const size = matrix.size;
      const bits = FormatInfo.getEncodedBits(errorCorrectionLevel, maskPattern);
      let i, mod;
      for (i = 0; i < 15; i++) {
        mod = (bits >> i & 1) === 1;
        if (i < 6) {
          matrix.set(i, 8, mod, true);
        } else if (i < 8) {
          matrix.set(i + 1, 8, mod, true);
        } else {
          matrix.set(size - 15 + i, 8, mod, true);
        }
        if (i < 8) {
          matrix.set(8, size - i - 1, mod, true);
        } else if (i < 9) {
          matrix.set(8, 15 - i - 1 + 1, mod, true);
        } else {
          matrix.set(8, 15 - i - 1, mod, true);
        }
      }
      matrix.set(size - 8, 8, 1, true);
    }
    function setupData(matrix, data) {
      const size = matrix.size;
      let inc = -1;
      let row = size - 1;
      let bitIndex = 7;
      let byteIndex = 0;
      for (let col = size - 1; col > 0; col -= 2) {
        if (col === 6) col--;
        while (true) {
          for (let c = 0; c < 2; c++) {
            if (!matrix.isReserved(row, col - c)) {
              let dark = false;
              if (byteIndex < data.length) {
                dark = (data[byteIndex] >>> bitIndex & 1) === 1;
              }
              matrix.set(row, col - c, dark);
              bitIndex--;
              if (bitIndex === -1) {
                byteIndex++;
                bitIndex = 7;
              }
            }
          }
          row += inc;
          if (row < 0 || size <= row) {
            row -= inc;
            inc = -inc;
            break;
          }
        }
      }
    }
    function createData(version, errorCorrectionLevel, segments) {
      const buffer = new BitBuffer();
      segments.forEach(function(data) {
        buffer.put(data.mode.bit, 4);
        buffer.put(data.getLength(), Mode.getCharCountIndicator(data.mode, version));
        data.write(buffer);
      });
      const totalCodewords = Utils.getSymbolTotalCodewords(version);
      const ecTotalCodewords = ECCode.getTotalCodewordsCount(version, errorCorrectionLevel);
      const dataTotalCodewordsBits = (totalCodewords - ecTotalCodewords) * 8;
      if (buffer.getLengthInBits() + 4 <= dataTotalCodewordsBits) {
        buffer.put(0, 4);
      }
      while (buffer.getLengthInBits() % 8 !== 0) {
        buffer.putBit(0);
      }
      const remainingByte = (dataTotalCodewordsBits - buffer.getLengthInBits()) / 8;
      for (let i = 0; i < remainingByte; i++) {
        buffer.put(i % 2 ? 17 : 236, 8);
      }
      return createCodewords(buffer, version, errorCorrectionLevel);
    }
    function createCodewords(bitBuffer, version, errorCorrectionLevel) {
      const totalCodewords = Utils.getSymbolTotalCodewords(version);
      const ecTotalCodewords = ECCode.getTotalCodewordsCount(version, errorCorrectionLevel);
      const dataTotalCodewords = totalCodewords - ecTotalCodewords;
      const ecTotalBlocks = ECCode.getBlocksCount(version, errorCorrectionLevel);
      const blocksInGroup2 = totalCodewords % ecTotalBlocks;
      const blocksInGroup1 = ecTotalBlocks - blocksInGroup2;
      const totalCodewordsInGroup1 = Math.floor(totalCodewords / ecTotalBlocks);
      const dataCodewordsInGroup1 = Math.floor(dataTotalCodewords / ecTotalBlocks);
      const dataCodewordsInGroup2 = dataCodewordsInGroup1 + 1;
      const ecCount = totalCodewordsInGroup1 - dataCodewordsInGroup1;
      const rs = new ReedSolomonEncoder(ecCount);
      let offset = 0;
      const dcData = new Array(ecTotalBlocks);
      const ecData = new Array(ecTotalBlocks);
      let maxDataSize = 0;
      const buffer = new Uint8Array(bitBuffer.buffer);
      for (let b = 0; b < ecTotalBlocks; b++) {
        const dataSize = b < blocksInGroup1 ? dataCodewordsInGroup1 : dataCodewordsInGroup2;
        dcData[b] = buffer.slice(offset, offset + dataSize);
        ecData[b] = rs.encode(dcData[b]);
        offset += dataSize;
        maxDataSize = Math.max(maxDataSize, dataSize);
      }
      const data = new Uint8Array(totalCodewords);
      let index = 0;
      let i, r;
      for (i = 0; i < maxDataSize; i++) {
        for (r = 0; r < ecTotalBlocks; r++) {
          if (i < dcData[r].length) {
            data[index++] = dcData[r][i];
          }
        }
      }
      for (i = 0; i < ecCount; i++) {
        for (r = 0; r < ecTotalBlocks; r++) {
          data[index++] = ecData[r][i];
        }
      }
      return data;
    }
    function createSymbol(data, version, errorCorrectionLevel, maskPattern) {
      let segments;
      if (Array.isArray(data)) {
        segments = Segments.fromArray(data);
      } else if (typeof data === "string") {
        let estimatedVersion = version;
        if (!estimatedVersion) {
          const rawSegments = Segments.rawSplit(data);
          estimatedVersion = Version.getBestVersionForData(rawSegments, errorCorrectionLevel);
        }
        segments = Segments.fromString(data, estimatedVersion || 40);
      } else {
        throw new Error("Invalid data");
      }
      const bestVersion = Version.getBestVersionForData(segments, errorCorrectionLevel);
      if (!bestVersion) {
        throw new Error("The amount of data is too big to be stored in a QR Code");
      }
      if (!version) {
        version = bestVersion;
      } else if (version < bestVersion) {
        throw new Error(
          "\nThe chosen QR Code version cannot contain this amount of data.\nMinimum version required to store current data is: " + bestVersion + ".\n"
        );
      }
      const dataBits = createData(version, errorCorrectionLevel, segments);
      const moduleCount = Utils.getSymbolSize(version);
      const modules = new BitMatrix(moduleCount);
      setupFinderPattern(modules, version);
      setupTimingPattern(modules);
      setupAlignmentPattern(modules, version);
      setupFormatInfo(modules, errorCorrectionLevel, 0);
      if (version >= 7) {
        setupVersionInfo(modules, version);
      }
      setupData(modules, dataBits);
      if (isNaN(maskPattern)) {
        maskPattern = MaskPattern.getBestMask(
          modules,
          setupFormatInfo.bind(null, modules, errorCorrectionLevel)
        );
      }
      MaskPattern.applyMask(maskPattern, modules);
      setupFormatInfo(modules, errorCorrectionLevel, maskPattern);
      return {
        modules,
        version,
        errorCorrectionLevel,
        maskPattern,
        segments
      };
    }
    exports2.create = function create(data, options) {
      if (typeof data === "undefined" || data === "") {
        throw new Error("No input text");
      }
      let errorCorrectionLevel = ECLevel.M;
      let version;
      let mask;
      if (typeof options !== "undefined") {
        errorCorrectionLevel = ECLevel.from(options.errorCorrectionLevel, ECLevel.M);
        version = Version.from(options.version);
        mask = MaskPattern.from(options.maskPattern);
        if (options.toSJISFunc) {
          Utils.setToSJISFunction(options.toSJISFunc);
        }
      }
      return createSymbol(data, version, errorCorrectionLevel, mask);
    };
  }
});

// node_modules/pngjs/lib/chunkstream.js
var require_chunkstream = __commonJS({
  "node_modules/pngjs/lib/chunkstream.js"(exports2, module2) {
    "use strict";
    var util = require("util");
    var Stream = require("stream");
    var ChunkStream = module2.exports = function() {
      Stream.call(this);
      this._buffers = [];
      this._buffered = 0;
      this._reads = [];
      this._paused = false;
      this._encoding = "utf8";
      this.writable = true;
    };
    util.inherits(ChunkStream, Stream);
    ChunkStream.prototype.read = function(length, callback) {
      this._reads.push({
        length: Math.abs(length),
        // if length < 0 then at most this length
        allowLess: length < 0,
        func: callback
      });
      process.nextTick(
        function() {
          this._process();
          if (this._paused && this._reads && this._reads.length > 0) {
            this._paused = false;
            this.emit("drain");
          }
        }.bind(this)
      );
    };
    ChunkStream.prototype.write = function(data, encoding) {
      if (!this.writable) {
        this.emit("error", new Error("Stream not writable"));
        return false;
      }
      let dataBuffer;
      if (Buffer.isBuffer(data)) {
        dataBuffer = data;
      } else {
        dataBuffer = Buffer.from(data, encoding || this._encoding);
      }
      this._buffers.push(dataBuffer);
      this._buffered += dataBuffer.length;
      this._process();
      if (this._reads && this._reads.length === 0) {
        this._paused = true;
      }
      return this.writable && !this._paused;
    };
    ChunkStream.prototype.end = function(data, encoding) {
      if (data) {
        this.write(data, encoding);
      }
      this.writable = false;
      if (!this._buffers) {
        return;
      }
      if (this._buffers.length === 0) {
        this._end();
      } else {
        this._buffers.push(null);
        this._process();
      }
    };
    ChunkStream.prototype.destroySoon = ChunkStream.prototype.end;
    ChunkStream.prototype._end = function() {
      if (this._reads.length > 0) {
        this.emit("error", new Error("Unexpected end of input"));
      }
      this.destroy();
    };
    ChunkStream.prototype.destroy = function() {
      if (!this._buffers) {
        return;
      }
      this.writable = false;
      this._reads = null;
      this._buffers = null;
      this.emit("close");
    };
    ChunkStream.prototype._processReadAllowingLess = function(read) {
      this._reads.shift();
      let smallerBuf = this._buffers[0];
      if (smallerBuf.length > read.length) {
        this._buffered -= read.length;
        this._buffers[0] = smallerBuf.slice(read.length);
        read.func.call(this, smallerBuf.slice(0, read.length));
      } else {
        this._buffered -= smallerBuf.length;
        this._buffers.shift();
        read.func.call(this, smallerBuf);
      }
    };
    ChunkStream.prototype._processRead = function(read) {
      this._reads.shift();
      let pos = 0;
      let count = 0;
      let data = Buffer.alloc(read.length);
      while (pos < read.length) {
        let buf = this._buffers[count++];
        let len = Math.min(buf.length, read.length - pos);
        buf.copy(data, pos, 0, len);
        pos += len;
        if (len !== buf.length) {
          this._buffers[--count] = buf.slice(len);
        }
      }
      if (count > 0) {
        this._buffers.splice(0, count);
      }
      this._buffered -= read.length;
      read.func.call(this, data);
    };
    ChunkStream.prototype._process = function() {
      try {
        while (this._buffered > 0 && this._reads && this._reads.length > 0) {
          let read = this._reads[0];
          if (read.allowLess) {
            this._processReadAllowingLess(read);
          } else if (this._buffered >= read.length) {
            this._processRead(read);
          } else {
            break;
          }
        }
        if (this._buffers && !this.writable) {
          this._end();
        }
      } catch (ex) {
        this.emit("error", ex);
      }
    };
  }
});

// node_modules/pngjs/lib/interlace.js
var require_interlace = __commonJS({
  "node_modules/pngjs/lib/interlace.js"(exports2) {
    "use strict";
    var imagePasses = [
      {
        // pass 1 - 1px
        x: [0],
        y: [0]
      },
      {
        // pass 2 - 1px
        x: [4],
        y: [0]
      },
      {
        // pass 3 - 2px
        x: [0, 4],
        y: [4]
      },
      {
        // pass 4 - 4px
        x: [2, 6],
        y: [0, 4]
      },
      {
        // pass 5 - 8px
        x: [0, 2, 4, 6],
        y: [2, 6]
      },
      {
        // pass 6 - 16px
        x: [1, 3, 5, 7],
        y: [0, 2, 4, 6]
      },
      {
        // pass 7 - 32px
        x: [0, 1, 2, 3, 4, 5, 6, 7],
        y: [1, 3, 5, 7]
      }
    ];
    exports2.getImagePasses = function(width, height) {
      let images = [];
      let xLeftOver = width % 8;
      let yLeftOver = height % 8;
      let xRepeats = (width - xLeftOver) / 8;
      let yRepeats = (height - yLeftOver) / 8;
      for (let i = 0; i < imagePasses.length; i++) {
        let pass = imagePasses[i];
        let passWidth = xRepeats * pass.x.length;
        let passHeight = yRepeats * pass.y.length;
        for (let j = 0; j < pass.x.length; j++) {
          if (pass.x[j] < xLeftOver) {
            passWidth++;
          } else {
            break;
          }
        }
        for (let j = 0; j < pass.y.length; j++) {
          if (pass.y[j] < yLeftOver) {
            passHeight++;
          } else {
            break;
          }
        }
        if (passWidth > 0 && passHeight > 0) {
          images.push({ width: passWidth, height: passHeight, index: i });
        }
      }
      return images;
    };
    exports2.getInterlaceIterator = function(width) {
      return function(x, y, pass) {
        let outerXLeftOver = x % imagePasses[pass].x.length;
        let outerX = (x - outerXLeftOver) / imagePasses[pass].x.length * 8 + imagePasses[pass].x[outerXLeftOver];
        let outerYLeftOver = y % imagePasses[pass].y.length;
        let outerY = (y - outerYLeftOver) / imagePasses[pass].y.length * 8 + imagePasses[pass].y[outerYLeftOver];
        return outerX * 4 + outerY * width * 4;
      };
    };
  }
});

// node_modules/pngjs/lib/paeth-predictor.js
var require_paeth_predictor = __commonJS({
  "node_modules/pngjs/lib/paeth-predictor.js"(exports2, module2) {
    "use strict";
    module2.exports = function paethPredictor(left, above, upLeft) {
      let paeth = left + above - upLeft;
      let pLeft = Math.abs(paeth - left);
      let pAbove = Math.abs(paeth - above);
      let pUpLeft = Math.abs(paeth - upLeft);
      if (pLeft <= pAbove && pLeft <= pUpLeft) {
        return left;
      }
      if (pAbove <= pUpLeft) {
        return above;
      }
      return upLeft;
    };
  }
});

// node_modules/pngjs/lib/filter-parse.js
var require_filter_parse = __commonJS({
  "node_modules/pngjs/lib/filter-parse.js"(exports2, module2) {
    "use strict";
    var interlaceUtils = require_interlace();
    var paethPredictor = require_paeth_predictor();
    function getByteWidth(width, bpp, depth) {
      let byteWidth = width * bpp;
      if (depth !== 8) {
        byteWidth = Math.ceil(byteWidth / (8 / depth));
      }
      return byteWidth;
    }
    var Filter = module2.exports = function(bitmapInfo, dependencies) {
      let width = bitmapInfo.width;
      let height = bitmapInfo.height;
      let interlace = bitmapInfo.interlace;
      let bpp = bitmapInfo.bpp;
      let depth = bitmapInfo.depth;
      this.read = dependencies.read;
      this.write = dependencies.write;
      this.complete = dependencies.complete;
      this._imageIndex = 0;
      this._images = [];
      if (interlace) {
        let passes = interlaceUtils.getImagePasses(width, height);
        for (let i = 0; i < passes.length; i++) {
          this._images.push({
            byteWidth: getByteWidth(passes[i].width, bpp, depth),
            height: passes[i].height,
            lineIndex: 0
          });
        }
      } else {
        this._images.push({
          byteWidth: getByteWidth(width, bpp, depth),
          height,
          lineIndex: 0
        });
      }
      if (depth === 8) {
        this._xComparison = bpp;
      } else if (depth === 16) {
        this._xComparison = bpp * 2;
      } else {
        this._xComparison = 1;
      }
    };
    Filter.prototype.start = function() {
      this.read(
        this._images[this._imageIndex].byteWidth + 1,
        this._reverseFilterLine.bind(this)
      );
    };
    Filter.prototype._unFilterType1 = function(rawData, unfilteredLine, byteWidth) {
      let xComparison = this._xComparison;
      let xBiggerThan = xComparison - 1;
      for (let x = 0; x < byteWidth; x++) {
        let rawByte = rawData[1 + x];
        let f1Left = x > xBiggerThan ? unfilteredLine[x - xComparison] : 0;
        unfilteredLine[x] = rawByte + f1Left;
      }
    };
    Filter.prototype._unFilterType2 = function(rawData, unfilteredLine, byteWidth) {
      let lastLine = this._lastLine;
      for (let x = 0; x < byteWidth; x++) {
        let rawByte = rawData[1 + x];
        let f2Up = lastLine ? lastLine[x] : 0;
        unfilteredLine[x] = rawByte + f2Up;
      }
    };
    Filter.prototype._unFilterType3 = function(rawData, unfilteredLine, byteWidth) {
      let xComparison = this._xComparison;
      let xBiggerThan = xComparison - 1;
      let lastLine = this._lastLine;
      for (let x = 0; x < byteWidth; x++) {
        let rawByte = rawData[1 + x];
        let f3Up = lastLine ? lastLine[x] : 0;
        let f3Left = x > xBiggerThan ? unfilteredLine[x - xComparison] : 0;
        let f3Add = Math.floor((f3Left + f3Up) / 2);
        unfilteredLine[x] = rawByte + f3Add;
      }
    };
    Filter.prototype._unFilterType4 = function(rawData, unfilteredLine, byteWidth) {
      let xComparison = this._xComparison;
      let xBiggerThan = xComparison - 1;
      let lastLine = this._lastLine;
      for (let x = 0; x < byteWidth; x++) {
        let rawByte = rawData[1 + x];
        let f4Up = lastLine ? lastLine[x] : 0;
        let f4Left = x > xBiggerThan ? unfilteredLine[x - xComparison] : 0;
        let f4UpLeft = x > xBiggerThan && lastLine ? lastLine[x - xComparison] : 0;
        let f4Add = paethPredictor(f4Left, f4Up, f4UpLeft);
        unfilteredLine[x] = rawByte + f4Add;
      }
    };
    Filter.prototype._reverseFilterLine = function(rawData) {
      let filter = rawData[0];
      let unfilteredLine;
      let currentImage = this._images[this._imageIndex];
      let byteWidth = currentImage.byteWidth;
      if (filter === 0) {
        unfilteredLine = rawData.slice(1, byteWidth + 1);
      } else {
        unfilteredLine = Buffer.alloc(byteWidth);
        switch (filter) {
          case 1:
            this._unFilterType1(rawData, unfilteredLine, byteWidth);
            break;
          case 2:
            this._unFilterType2(rawData, unfilteredLine, byteWidth);
            break;
          case 3:
            this._unFilterType3(rawData, unfilteredLine, byteWidth);
            break;
          case 4:
            this._unFilterType4(rawData, unfilteredLine, byteWidth);
            break;
          default:
            throw new Error("Unrecognised filter type - " + filter);
        }
      }
      this.write(unfilteredLine);
      currentImage.lineIndex++;
      if (currentImage.lineIndex >= currentImage.height) {
        this._lastLine = null;
        this._imageIndex++;
        currentImage = this._images[this._imageIndex];
      } else {
        this._lastLine = unfilteredLine;
      }
      if (currentImage) {
        this.read(currentImage.byteWidth + 1, this._reverseFilterLine.bind(this));
      } else {
        this._lastLine = null;
        this.complete();
      }
    };
  }
});

// node_modules/pngjs/lib/filter-parse-async.js
var require_filter_parse_async = __commonJS({
  "node_modules/pngjs/lib/filter-parse-async.js"(exports2, module2) {
    "use strict";
    var util = require("util");
    var ChunkStream = require_chunkstream();
    var Filter = require_filter_parse();
    var FilterAsync = module2.exports = function(bitmapInfo) {
      ChunkStream.call(this);
      let buffers = [];
      let that = this;
      this._filter = new Filter(bitmapInfo, {
        read: this.read.bind(this),
        write: function(buffer) {
          buffers.push(buffer);
        },
        complete: function() {
          that.emit("complete", Buffer.concat(buffers));
        }
      });
      this._filter.start();
    };
    util.inherits(FilterAsync, ChunkStream);
  }
});

// node_modules/pngjs/lib/constants.js
var require_constants = __commonJS({
  "node_modules/pngjs/lib/constants.js"(exports2, module2) {
    "use strict";
    module2.exports = {
      PNG_SIGNATURE: [137, 80, 78, 71, 13, 10, 26, 10],
      TYPE_IHDR: 1229472850,
      TYPE_IEND: 1229278788,
      TYPE_IDAT: 1229209940,
      TYPE_PLTE: 1347179589,
      TYPE_tRNS: 1951551059,
      // eslint-disable-line camelcase
      TYPE_gAMA: 1732332865,
      // eslint-disable-line camelcase
      // color-type bits
      COLORTYPE_GRAYSCALE: 0,
      COLORTYPE_PALETTE: 1,
      COLORTYPE_COLOR: 2,
      COLORTYPE_ALPHA: 4,
      // e.g. grayscale and alpha
      // color-type combinations
      COLORTYPE_PALETTE_COLOR: 3,
      COLORTYPE_COLOR_ALPHA: 6,
      COLORTYPE_TO_BPP_MAP: {
        0: 1,
        2: 3,
        3: 1,
        4: 2,
        6: 4
      },
      GAMMA_DIVISION: 1e5
    };
  }
});

// node_modules/pngjs/lib/crc.js
var require_crc = __commonJS({
  "node_modules/pngjs/lib/crc.js"(exports2, module2) {
    "use strict";
    var crcTable = [];
    (function() {
      for (let i = 0; i < 256; i++) {
        let currentCrc = i;
        for (let j = 0; j < 8; j++) {
          if (currentCrc & 1) {
            currentCrc = 3988292384 ^ currentCrc >>> 1;
          } else {
            currentCrc = currentCrc >>> 1;
          }
        }
        crcTable[i] = currentCrc;
      }
    })();
    var CrcCalculator = module2.exports = function() {
      this._crc = -1;
    };
    CrcCalculator.prototype.write = function(data) {
      for (let i = 0; i < data.length; i++) {
        this._crc = crcTable[(this._crc ^ data[i]) & 255] ^ this._crc >>> 8;
      }
      return true;
    };
    CrcCalculator.prototype.crc32 = function() {
      return this._crc ^ -1;
    };
    CrcCalculator.crc32 = function(buf) {
      let crc = -1;
      for (let i = 0; i < buf.length; i++) {
        crc = crcTable[(crc ^ buf[i]) & 255] ^ crc >>> 8;
      }
      return crc ^ -1;
    };
  }
});

// node_modules/pngjs/lib/parser.js
var require_parser = __commonJS({
  "node_modules/pngjs/lib/parser.js"(exports2, module2) {
    "use strict";
    var constants = require_constants();
    var CrcCalculator = require_crc();
    var Parser = module2.exports = function(options, dependencies) {
      this._options = options;
      options.checkCRC = options.checkCRC !== false;
      this._hasIHDR = false;
      this._hasIEND = false;
      this._emittedHeadersFinished = false;
      this._palette = [];
      this._colorType = 0;
      this._chunks = {};
      this._chunks[constants.TYPE_IHDR] = this._handleIHDR.bind(this);
      this._chunks[constants.TYPE_IEND] = this._handleIEND.bind(this);
      this._chunks[constants.TYPE_IDAT] = this._handleIDAT.bind(this);
      this._chunks[constants.TYPE_PLTE] = this._handlePLTE.bind(this);
      this._chunks[constants.TYPE_tRNS] = this._handleTRNS.bind(this);
      this._chunks[constants.TYPE_gAMA] = this._handleGAMA.bind(this);
      this.read = dependencies.read;
      this.error = dependencies.error;
      this.metadata = dependencies.metadata;
      this.gamma = dependencies.gamma;
      this.transColor = dependencies.transColor;
      this.palette = dependencies.palette;
      this.parsed = dependencies.parsed;
      this.inflateData = dependencies.inflateData;
      this.finished = dependencies.finished;
      this.simpleTransparency = dependencies.simpleTransparency;
      this.headersFinished = dependencies.headersFinished || function() {
      };
    };
    Parser.prototype.start = function() {
      this.read(constants.PNG_SIGNATURE.length, this._parseSignature.bind(this));
    };
    Parser.prototype._parseSignature = function(data) {
      let signature = constants.PNG_SIGNATURE;
      for (let i = 0; i < signature.length; i++) {
        if (data[i] !== signature[i]) {
          this.error(new Error("Invalid file signature"));
          return;
        }
      }
      this.read(8, this._parseChunkBegin.bind(this));
    };
    Parser.prototype._parseChunkBegin = function(data) {
      let length = data.readUInt32BE(0);
      let type = data.readUInt32BE(4);
      let name = "";
      for (let i = 4; i < 8; i++) {
        name += String.fromCharCode(data[i]);
      }
      let ancillary = Boolean(data[4] & 32);
      if (!this._hasIHDR && type !== constants.TYPE_IHDR) {
        this.error(new Error("Expected IHDR on beggining"));
        return;
      }
      this._crc = new CrcCalculator();
      this._crc.write(Buffer.from(name));
      if (this._chunks[type]) {
        return this._chunks[type](length);
      }
      if (!ancillary) {
        this.error(new Error("Unsupported critical chunk type " + name));
        return;
      }
      this.read(length + 4, this._skipChunk.bind(this));
    };
    Parser.prototype._skipChunk = function() {
      this.read(8, this._parseChunkBegin.bind(this));
    };
    Parser.prototype._handleChunkEnd = function() {
      this.read(4, this._parseChunkEnd.bind(this));
    };
    Parser.prototype._parseChunkEnd = function(data) {
      let fileCrc = data.readInt32BE(0);
      let calcCrc = this._crc.crc32();
      if (this._options.checkCRC && calcCrc !== fileCrc) {
        this.error(new Error("Crc error - " + fileCrc + " - " + calcCrc));
        return;
      }
      if (!this._hasIEND) {
        this.read(8, this._parseChunkBegin.bind(this));
      }
    };
    Parser.prototype._handleIHDR = function(length) {
      this.read(length, this._parseIHDR.bind(this));
    };
    Parser.prototype._parseIHDR = function(data) {
      this._crc.write(data);
      let width = data.readUInt32BE(0);
      let height = data.readUInt32BE(4);
      let depth = data[8];
      let colorType = data[9];
      let compr = data[10];
      let filter = data[11];
      let interlace = data[12];
      if (depth !== 8 && depth !== 4 && depth !== 2 && depth !== 1 && depth !== 16) {
        this.error(new Error("Unsupported bit depth " + depth));
        return;
      }
      if (!(colorType in constants.COLORTYPE_TO_BPP_MAP)) {
        this.error(new Error("Unsupported color type"));
        return;
      }
      if (compr !== 0) {
        this.error(new Error("Unsupported compression method"));
        return;
      }
      if (filter !== 0) {
        this.error(new Error("Unsupported filter method"));
        return;
      }
      if (interlace !== 0 && interlace !== 1) {
        this.error(new Error("Unsupported interlace method"));
        return;
      }
      this._colorType = colorType;
      let bpp = constants.COLORTYPE_TO_BPP_MAP[this._colorType];
      this._hasIHDR = true;
      this.metadata({
        width,
        height,
        depth,
        interlace: Boolean(interlace),
        palette: Boolean(colorType & constants.COLORTYPE_PALETTE),
        color: Boolean(colorType & constants.COLORTYPE_COLOR),
        alpha: Boolean(colorType & constants.COLORTYPE_ALPHA),
        bpp,
        colorType
      });
      this._handleChunkEnd();
    };
    Parser.prototype._handlePLTE = function(length) {
      this.read(length, this._parsePLTE.bind(this));
    };
    Parser.prototype._parsePLTE = function(data) {
      this._crc.write(data);
      let entries = Math.floor(data.length / 3);
      for (let i = 0; i < entries; i++) {
        this._palette.push([data[i * 3], data[i * 3 + 1], data[i * 3 + 2], 255]);
      }
      this.palette(this._palette);
      this._handleChunkEnd();
    };
    Parser.prototype._handleTRNS = function(length) {
      this.simpleTransparency();
      this.read(length, this._parseTRNS.bind(this));
    };
    Parser.prototype._parseTRNS = function(data) {
      this._crc.write(data);
      if (this._colorType === constants.COLORTYPE_PALETTE_COLOR) {
        if (this._palette.length === 0) {
          this.error(new Error("Transparency chunk must be after palette"));
          return;
        }
        if (data.length > this._palette.length) {
          this.error(new Error("More transparent colors than palette size"));
          return;
        }
        for (let i = 0; i < data.length; i++) {
          this._palette[i][3] = data[i];
        }
        this.palette(this._palette);
      }
      if (this._colorType === constants.COLORTYPE_GRAYSCALE) {
        this.transColor([data.readUInt16BE(0)]);
      }
      if (this._colorType === constants.COLORTYPE_COLOR) {
        this.transColor([
          data.readUInt16BE(0),
          data.readUInt16BE(2),
          data.readUInt16BE(4)
        ]);
      }
      this._handleChunkEnd();
    };
    Parser.prototype._handleGAMA = function(length) {
      this.read(length, this._parseGAMA.bind(this));
    };
    Parser.prototype._parseGAMA = function(data) {
      this._crc.write(data);
      this.gamma(data.readUInt32BE(0) / constants.GAMMA_DIVISION);
      this._handleChunkEnd();
    };
    Parser.prototype._handleIDAT = function(length) {
      if (!this._emittedHeadersFinished) {
        this._emittedHeadersFinished = true;
        this.headersFinished();
      }
      this.read(-length, this._parseIDAT.bind(this, length));
    };
    Parser.prototype._parseIDAT = function(length, data) {
      this._crc.write(data);
      if (this._colorType === constants.COLORTYPE_PALETTE_COLOR && this._palette.length === 0) {
        throw new Error("Expected palette not found");
      }
      this.inflateData(data);
      let leftOverLength = length - data.length;
      if (leftOverLength > 0) {
        this._handleIDAT(leftOverLength);
      } else {
        this._handleChunkEnd();
      }
    };
    Parser.prototype._handleIEND = function(length) {
      this.read(length, this._parseIEND.bind(this));
    };
    Parser.prototype._parseIEND = function(data) {
      this._crc.write(data);
      this._hasIEND = true;
      this._handleChunkEnd();
      if (this.finished) {
        this.finished();
      }
    };
  }
});

// node_modules/pngjs/lib/bitmapper.js
var require_bitmapper = __commonJS({
  "node_modules/pngjs/lib/bitmapper.js"(exports2) {
    "use strict";
    var interlaceUtils = require_interlace();
    var pixelBppMapper = [
      // 0 - dummy entry
      function() {
      },
      // 1 - L
      // 0: 0, 1: 0, 2: 0, 3: 0xff
      function(pxData, data, pxPos, rawPos) {
        if (rawPos === data.length) {
          throw new Error("Ran out of data");
        }
        let pixel = data[rawPos];
        pxData[pxPos] = pixel;
        pxData[pxPos + 1] = pixel;
        pxData[pxPos + 2] = pixel;
        pxData[pxPos + 3] = 255;
      },
      // 2 - LA
      // 0: 0, 1: 0, 2: 0, 3: 1
      function(pxData, data, pxPos, rawPos) {
        if (rawPos + 1 >= data.length) {
          throw new Error("Ran out of data");
        }
        let pixel = data[rawPos];
        pxData[pxPos] = pixel;
        pxData[pxPos + 1] = pixel;
        pxData[pxPos + 2] = pixel;
        pxData[pxPos + 3] = data[rawPos + 1];
      },
      // 3 - RGB
      // 0: 0, 1: 1, 2: 2, 3: 0xff
      function(pxData, data, pxPos, rawPos) {
        if (rawPos + 2 >= data.length) {
          throw new Error("Ran out of data");
        }
        pxData[pxPos] = data[rawPos];
        pxData[pxPos + 1] = data[rawPos + 1];
        pxData[pxPos + 2] = data[rawPos + 2];
        pxData[pxPos + 3] = 255;
      },
      // 4 - RGBA
      // 0: 0, 1: 1, 2: 2, 3: 3
      function(pxData, data, pxPos, rawPos) {
        if (rawPos + 3 >= data.length) {
          throw new Error("Ran out of data");
        }
        pxData[pxPos] = data[rawPos];
        pxData[pxPos + 1] = data[rawPos + 1];
        pxData[pxPos + 2] = data[rawPos + 2];
        pxData[pxPos + 3] = data[rawPos + 3];
      }
    ];
    var pixelBppCustomMapper = [
      // 0 - dummy entry
      function() {
      },
      // 1 - L
      // 0: 0, 1: 0, 2: 0, 3: 0xff
      function(pxData, pixelData, pxPos, maxBit) {
        let pixel = pixelData[0];
        pxData[pxPos] = pixel;
        pxData[pxPos + 1] = pixel;
        pxData[pxPos + 2] = pixel;
        pxData[pxPos + 3] = maxBit;
      },
      // 2 - LA
      // 0: 0, 1: 0, 2: 0, 3: 1
      function(pxData, pixelData, pxPos) {
        let pixel = pixelData[0];
        pxData[pxPos] = pixel;
        pxData[pxPos + 1] = pixel;
        pxData[pxPos + 2] = pixel;
        pxData[pxPos + 3] = pixelData[1];
      },
      // 3 - RGB
      // 0: 0, 1: 1, 2: 2, 3: 0xff
      function(pxData, pixelData, pxPos, maxBit) {
        pxData[pxPos] = pixelData[0];
        pxData[pxPos + 1] = pixelData[1];
        pxData[pxPos + 2] = pixelData[2];
        pxData[pxPos + 3] = maxBit;
      },
      // 4 - RGBA
      // 0: 0, 1: 1, 2: 2, 3: 3
      function(pxData, pixelData, pxPos) {
        pxData[pxPos] = pixelData[0];
        pxData[pxPos + 1] = pixelData[1];
        pxData[pxPos + 2] = pixelData[2];
        pxData[pxPos + 3] = pixelData[3];
      }
    ];
    function bitRetriever(data, depth) {
      let leftOver = [];
      let i = 0;
      function split() {
        if (i === data.length) {
          throw new Error("Ran out of data");
        }
        let byte = data[i];
        i++;
        let byte8, byte7, byte6, byte5, byte4, byte3, byte2, byte1;
        switch (depth) {
          default:
            throw new Error("unrecognised depth");
          case 16:
            byte2 = data[i];
            i++;
            leftOver.push((byte << 8) + byte2);
            break;
          case 4:
            byte2 = byte & 15;
            byte1 = byte >> 4;
            leftOver.push(byte1, byte2);
            break;
          case 2:
            byte4 = byte & 3;
            byte3 = byte >> 2 & 3;
            byte2 = byte >> 4 & 3;
            byte1 = byte >> 6 & 3;
            leftOver.push(byte1, byte2, byte3, byte4);
            break;
          case 1:
            byte8 = byte & 1;
            byte7 = byte >> 1 & 1;
            byte6 = byte >> 2 & 1;
            byte5 = byte >> 3 & 1;
            byte4 = byte >> 4 & 1;
            byte3 = byte >> 5 & 1;
            byte2 = byte >> 6 & 1;
            byte1 = byte >> 7 & 1;
            leftOver.push(byte1, byte2, byte3, byte4, byte5, byte6, byte7, byte8);
            break;
        }
      }
      return {
        get: function(count) {
          while (leftOver.length < count) {
            split();
          }
          let returner = leftOver.slice(0, count);
          leftOver = leftOver.slice(count);
          return returner;
        },
        resetAfterLine: function() {
          leftOver.length = 0;
        },
        end: function() {
          if (i !== data.length) {
            throw new Error("extra data found");
          }
        }
      };
    }
    function mapImage8Bit(image, pxData, getPxPos, bpp, data, rawPos) {
      let imageWidth = image.width;
      let imageHeight = image.height;
      let imagePass = image.index;
      for (let y = 0; y < imageHeight; y++) {
        for (let x = 0; x < imageWidth; x++) {
          let pxPos = getPxPos(x, y, imagePass);
          pixelBppMapper[bpp](pxData, data, pxPos, rawPos);
          rawPos += bpp;
        }
      }
      return rawPos;
    }
    function mapImageCustomBit(image, pxData, getPxPos, bpp, bits, maxBit) {
      let imageWidth = image.width;
      let imageHeight = image.height;
      let imagePass = image.index;
      for (let y = 0; y < imageHeight; y++) {
        for (let x = 0; x < imageWidth; x++) {
          let pixelData = bits.get(bpp);
          let pxPos = getPxPos(x, y, imagePass);
          pixelBppCustomMapper[bpp](pxData, pixelData, pxPos, maxBit);
        }
        bits.resetAfterLine();
      }
    }
    exports2.dataToBitMap = function(data, bitmapInfo) {
      let width = bitmapInfo.width;
      let height = bitmapInfo.height;
      let depth = bitmapInfo.depth;
      let bpp = bitmapInfo.bpp;
      let interlace = bitmapInfo.interlace;
      let bits;
      if (depth !== 8) {
        bits = bitRetriever(data, depth);
      }
      let pxData;
      if (depth <= 8) {
        pxData = Buffer.alloc(width * height * 4);
      } else {
        pxData = new Uint16Array(width * height * 4);
      }
      let maxBit = Math.pow(2, depth) - 1;
      let rawPos = 0;
      let images;
      let getPxPos;
      if (interlace) {
        images = interlaceUtils.getImagePasses(width, height);
        getPxPos = interlaceUtils.getInterlaceIterator(width, height);
      } else {
        let nonInterlacedPxPos = 0;
        getPxPos = function() {
          let returner = nonInterlacedPxPos;
          nonInterlacedPxPos += 4;
          return returner;
        };
        images = [{ width, height }];
      }
      for (let imageIndex = 0; imageIndex < images.length; imageIndex++) {
        if (depth === 8) {
          rawPos = mapImage8Bit(
            images[imageIndex],
            pxData,
            getPxPos,
            bpp,
            data,
            rawPos
          );
        } else {
          mapImageCustomBit(
            images[imageIndex],
            pxData,
            getPxPos,
            bpp,
            bits,
            maxBit
          );
        }
      }
      if (depth === 8) {
        if (rawPos !== data.length) {
          throw new Error("extra data found");
        }
      } else {
        bits.end();
      }
      return pxData;
    };
  }
});

// node_modules/pngjs/lib/format-normaliser.js
var require_format_normaliser = __commonJS({
  "node_modules/pngjs/lib/format-normaliser.js"(exports2, module2) {
    "use strict";
    function dePalette(indata, outdata, width, height, palette) {
      let pxPos = 0;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          let color = palette[indata[pxPos]];
          if (!color) {
            throw new Error("index " + indata[pxPos] + " not in palette");
          }
          for (let i = 0; i < 4; i++) {
            outdata[pxPos + i] = color[i];
          }
          pxPos += 4;
        }
      }
    }
    function replaceTransparentColor(indata, outdata, width, height, transColor) {
      let pxPos = 0;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          let makeTrans = false;
          if (transColor.length === 1) {
            if (transColor[0] === indata[pxPos]) {
              makeTrans = true;
            }
          } else if (transColor[0] === indata[pxPos] && transColor[1] === indata[pxPos + 1] && transColor[2] === indata[pxPos + 2]) {
            makeTrans = true;
          }
          if (makeTrans) {
            for (let i = 0; i < 4; i++) {
              outdata[pxPos + i] = 0;
            }
          }
          pxPos += 4;
        }
      }
    }
    function scaleDepth(indata, outdata, width, height, depth) {
      let maxOutSample = 255;
      let maxInSample = Math.pow(2, depth) - 1;
      let pxPos = 0;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          for (let i = 0; i < 4; i++) {
            outdata[pxPos + i] = Math.floor(
              indata[pxPos + i] * maxOutSample / maxInSample + 0.5
            );
          }
          pxPos += 4;
        }
      }
    }
    module2.exports = function(indata, imageData) {
      let depth = imageData.depth;
      let width = imageData.width;
      let height = imageData.height;
      let colorType = imageData.colorType;
      let transColor = imageData.transColor;
      let palette = imageData.palette;
      let outdata = indata;
      if (colorType === 3) {
        dePalette(indata, outdata, width, height, palette);
      } else {
        if (transColor) {
          replaceTransparentColor(indata, outdata, width, height, transColor);
        }
        if (depth !== 8) {
          if (depth === 16) {
            outdata = Buffer.alloc(width * height * 4);
          }
          scaleDepth(indata, outdata, width, height, depth);
        }
      }
      return outdata;
    };
  }
});

// node_modules/pngjs/lib/parser-async.js
var require_parser_async = __commonJS({
  "node_modules/pngjs/lib/parser-async.js"(exports2, module2) {
    "use strict";
    var util = require("util");
    var zlib = require("zlib");
    var ChunkStream = require_chunkstream();
    var FilterAsync = require_filter_parse_async();
    var Parser = require_parser();
    var bitmapper = require_bitmapper();
    var formatNormaliser = require_format_normaliser();
    var ParserAsync = module2.exports = function(options) {
      ChunkStream.call(this);
      this._parser = new Parser(options, {
        read: this.read.bind(this),
        error: this._handleError.bind(this),
        metadata: this._handleMetaData.bind(this),
        gamma: this.emit.bind(this, "gamma"),
        palette: this._handlePalette.bind(this),
        transColor: this._handleTransColor.bind(this),
        finished: this._finished.bind(this),
        inflateData: this._inflateData.bind(this),
        simpleTransparency: this._simpleTransparency.bind(this),
        headersFinished: this._headersFinished.bind(this)
      });
      this._options = options;
      this.writable = true;
      this._parser.start();
    };
    util.inherits(ParserAsync, ChunkStream);
    ParserAsync.prototype._handleError = function(err) {
      this.emit("error", err);
      this.writable = false;
      this.destroy();
      if (this._inflate && this._inflate.destroy) {
        this._inflate.destroy();
      }
      if (this._filter) {
        this._filter.destroy();
        this._filter.on("error", function() {
        });
      }
      this.errord = true;
    };
    ParserAsync.prototype._inflateData = function(data) {
      if (!this._inflate) {
        if (this._bitmapInfo.interlace) {
          this._inflate = zlib.createInflate();
          this._inflate.on("error", this.emit.bind(this, "error"));
          this._filter.on("complete", this._complete.bind(this));
          this._inflate.pipe(this._filter);
        } else {
          let rowSize = (this._bitmapInfo.width * this._bitmapInfo.bpp * this._bitmapInfo.depth + 7 >> 3) + 1;
          let imageSize = rowSize * this._bitmapInfo.height;
          let chunkSize = Math.max(imageSize, zlib.Z_MIN_CHUNK);
          this._inflate = zlib.createInflate({ chunkSize });
          let leftToInflate = imageSize;
          let emitError = this.emit.bind(this, "error");
          this._inflate.on("error", function(err) {
            if (!leftToInflate) {
              return;
            }
            emitError(err);
          });
          this._filter.on("complete", this._complete.bind(this));
          let filterWrite = this._filter.write.bind(this._filter);
          this._inflate.on("data", function(chunk) {
            if (!leftToInflate) {
              return;
            }
            if (chunk.length > leftToInflate) {
              chunk = chunk.slice(0, leftToInflate);
            }
            leftToInflate -= chunk.length;
            filterWrite(chunk);
          });
          this._inflate.on("end", this._filter.end.bind(this._filter));
        }
      }
      this._inflate.write(data);
    };
    ParserAsync.prototype._handleMetaData = function(metaData) {
      this._metaData = metaData;
      this._bitmapInfo = Object.create(metaData);
      this._filter = new FilterAsync(this._bitmapInfo);
    };
    ParserAsync.prototype._handleTransColor = function(transColor) {
      this._bitmapInfo.transColor = transColor;
    };
    ParserAsync.prototype._handlePalette = function(palette) {
      this._bitmapInfo.palette = palette;
    };
    ParserAsync.prototype._simpleTransparency = function() {
      this._metaData.alpha = true;
    };
    ParserAsync.prototype._headersFinished = function() {
      this.emit("metadata", this._metaData);
    };
    ParserAsync.prototype._finished = function() {
      if (this.errord) {
        return;
      }
      if (!this._inflate) {
        this.emit("error", "No Inflate block");
      } else {
        this._inflate.end();
      }
    };
    ParserAsync.prototype._complete = function(filteredData) {
      if (this.errord) {
        return;
      }
      let normalisedBitmapData;
      try {
        let bitmapData = bitmapper.dataToBitMap(filteredData, this._bitmapInfo);
        normalisedBitmapData = formatNormaliser(bitmapData, this._bitmapInfo);
        bitmapData = null;
      } catch (ex) {
        this._handleError(ex);
        return;
      }
      this.emit("parsed", normalisedBitmapData);
    };
  }
});

// node_modules/pngjs/lib/bitpacker.js
var require_bitpacker = __commonJS({
  "node_modules/pngjs/lib/bitpacker.js"(exports2, module2) {
    "use strict";
    var constants = require_constants();
    module2.exports = function(dataIn, width, height, options) {
      let outHasAlpha = [constants.COLORTYPE_COLOR_ALPHA, constants.COLORTYPE_ALPHA].indexOf(
        options.colorType
      ) !== -1;
      if (options.colorType === options.inputColorType) {
        let bigEndian = (function() {
          let buffer = new ArrayBuffer(2);
          new DataView(buffer).setInt16(
            0,
            256,
            true
            /* littleEndian */
          );
          return new Int16Array(buffer)[0] !== 256;
        })();
        if (options.bitDepth === 8 || options.bitDepth === 16 && bigEndian) {
          return dataIn;
        }
      }
      let data = options.bitDepth !== 16 ? dataIn : new Uint16Array(dataIn.buffer);
      let maxValue = 255;
      let inBpp = constants.COLORTYPE_TO_BPP_MAP[options.inputColorType];
      if (inBpp === 4 && !options.inputHasAlpha) {
        inBpp = 3;
      }
      let outBpp = constants.COLORTYPE_TO_BPP_MAP[options.colorType];
      if (options.bitDepth === 16) {
        maxValue = 65535;
        outBpp *= 2;
      }
      let outData = Buffer.alloc(width * height * outBpp);
      let inIndex = 0;
      let outIndex = 0;
      let bgColor = options.bgColor || {};
      if (bgColor.red === void 0) {
        bgColor.red = maxValue;
      }
      if (bgColor.green === void 0) {
        bgColor.green = maxValue;
      }
      if (bgColor.blue === void 0) {
        bgColor.blue = maxValue;
      }
      function getRGBA() {
        let red;
        let green;
        let blue;
        let alpha = maxValue;
        switch (options.inputColorType) {
          case constants.COLORTYPE_COLOR_ALPHA:
            alpha = data[inIndex + 3];
            red = data[inIndex];
            green = data[inIndex + 1];
            blue = data[inIndex + 2];
            break;
          case constants.COLORTYPE_COLOR:
            red = data[inIndex];
            green = data[inIndex + 1];
            blue = data[inIndex + 2];
            break;
          case constants.COLORTYPE_ALPHA:
            alpha = data[inIndex + 1];
            red = data[inIndex];
            green = red;
            blue = red;
            break;
          case constants.COLORTYPE_GRAYSCALE:
            red = data[inIndex];
            green = red;
            blue = red;
            break;
          default:
            throw new Error(
              "input color type:" + options.inputColorType + " is not supported at present"
            );
        }
        if (options.inputHasAlpha) {
          if (!outHasAlpha) {
            alpha /= maxValue;
            red = Math.min(
              Math.max(Math.round((1 - alpha) * bgColor.red + alpha * red), 0),
              maxValue
            );
            green = Math.min(
              Math.max(Math.round((1 - alpha) * bgColor.green + alpha * green), 0),
              maxValue
            );
            blue = Math.min(
              Math.max(Math.round((1 - alpha) * bgColor.blue + alpha * blue), 0),
              maxValue
            );
          }
        }
        return { red, green, blue, alpha };
      }
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          let rgba = getRGBA(data, inIndex);
          switch (options.colorType) {
            case constants.COLORTYPE_COLOR_ALPHA:
            case constants.COLORTYPE_COLOR:
              if (options.bitDepth === 8) {
                outData[outIndex] = rgba.red;
                outData[outIndex + 1] = rgba.green;
                outData[outIndex + 2] = rgba.blue;
                if (outHasAlpha) {
                  outData[outIndex + 3] = rgba.alpha;
                }
              } else {
                outData.writeUInt16BE(rgba.red, outIndex);
                outData.writeUInt16BE(rgba.green, outIndex + 2);
                outData.writeUInt16BE(rgba.blue, outIndex + 4);
                if (outHasAlpha) {
                  outData.writeUInt16BE(rgba.alpha, outIndex + 6);
                }
              }
              break;
            case constants.COLORTYPE_ALPHA:
            case constants.COLORTYPE_GRAYSCALE: {
              let grayscale = (rgba.red + rgba.green + rgba.blue) / 3;
              if (options.bitDepth === 8) {
                outData[outIndex] = grayscale;
                if (outHasAlpha) {
                  outData[outIndex + 1] = rgba.alpha;
                }
              } else {
                outData.writeUInt16BE(grayscale, outIndex);
                if (outHasAlpha) {
                  outData.writeUInt16BE(rgba.alpha, outIndex + 2);
                }
              }
              break;
            }
            default:
              throw new Error("unrecognised color Type " + options.colorType);
          }
          inIndex += inBpp;
          outIndex += outBpp;
        }
      }
      return outData;
    };
  }
});

// node_modules/pngjs/lib/filter-pack.js
var require_filter_pack = __commonJS({
  "node_modules/pngjs/lib/filter-pack.js"(exports2, module2) {
    "use strict";
    var paethPredictor = require_paeth_predictor();
    function filterNone(pxData, pxPos, byteWidth, rawData, rawPos) {
      for (let x = 0; x < byteWidth; x++) {
        rawData[rawPos + x] = pxData[pxPos + x];
      }
    }
    function filterSumNone(pxData, pxPos, byteWidth) {
      let sum = 0;
      let length = pxPos + byteWidth;
      for (let i = pxPos; i < length; i++) {
        sum += Math.abs(pxData[i]);
      }
      return sum;
    }
    function filterSub(pxData, pxPos, byteWidth, rawData, rawPos, bpp) {
      for (let x = 0; x < byteWidth; x++) {
        let left = x >= bpp ? pxData[pxPos + x - bpp] : 0;
        let val = pxData[pxPos + x] - left;
        rawData[rawPos + x] = val;
      }
    }
    function filterSumSub(pxData, pxPos, byteWidth, bpp) {
      let sum = 0;
      for (let x = 0; x < byteWidth; x++) {
        let left = x >= bpp ? pxData[pxPos + x - bpp] : 0;
        let val = pxData[pxPos + x] - left;
        sum += Math.abs(val);
      }
      return sum;
    }
    function filterUp(pxData, pxPos, byteWidth, rawData, rawPos) {
      for (let x = 0; x < byteWidth; x++) {
        let up = pxPos > 0 ? pxData[pxPos + x - byteWidth] : 0;
        let val = pxData[pxPos + x] - up;
        rawData[rawPos + x] = val;
      }
    }
    function filterSumUp(pxData, pxPos, byteWidth) {
      let sum = 0;
      let length = pxPos + byteWidth;
      for (let x = pxPos; x < length; x++) {
        let up = pxPos > 0 ? pxData[x - byteWidth] : 0;
        let val = pxData[x] - up;
        sum += Math.abs(val);
      }
      return sum;
    }
    function filterAvg(pxData, pxPos, byteWidth, rawData, rawPos, bpp) {
      for (let x = 0; x < byteWidth; x++) {
        let left = x >= bpp ? pxData[pxPos + x - bpp] : 0;
        let up = pxPos > 0 ? pxData[pxPos + x - byteWidth] : 0;
        let val = pxData[pxPos + x] - (left + up >> 1);
        rawData[rawPos + x] = val;
      }
    }
    function filterSumAvg(pxData, pxPos, byteWidth, bpp) {
      let sum = 0;
      for (let x = 0; x < byteWidth; x++) {
        let left = x >= bpp ? pxData[pxPos + x - bpp] : 0;
        let up = pxPos > 0 ? pxData[pxPos + x - byteWidth] : 0;
        let val = pxData[pxPos + x] - (left + up >> 1);
        sum += Math.abs(val);
      }
      return sum;
    }
    function filterPaeth(pxData, pxPos, byteWidth, rawData, rawPos, bpp) {
      for (let x = 0; x < byteWidth; x++) {
        let left = x >= bpp ? pxData[pxPos + x - bpp] : 0;
        let up = pxPos > 0 ? pxData[pxPos + x - byteWidth] : 0;
        let upleft = pxPos > 0 && x >= bpp ? pxData[pxPos + x - (byteWidth + bpp)] : 0;
        let val = pxData[pxPos + x] - paethPredictor(left, up, upleft);
        rawData[rawPos + x] = val;
      }
    }
    function filterSumPaeth(pxData, pxPos, byteWidth, bpp) {
      let sum = 0;
      for (let x = 0; x < byteWidth; x++) {
        let left = x >= bpp ? pxData[pxPos + x - bpp] : 0;
        let up = pxPos > 0 ? pxData[pxPos + x - byteWidth] : 0;
        let upleft = pxPos > 0 && x >= bpp ? pxData[pxPos + x - (byteWidth + bpp)] : 0;
        let val = pxData[pxPos + x] - paethPredictor(left, up, upleft);
        sum += Math.abs(val);
      }
      return sum;
    }
    var filters = {
      0: filterNone,
      1: filterSub,
      2: filterUp,
      3: filterAvg,
      4: filterPaeth
    };
    var filterSums = {
      0: filterSumNone,
      1: filterSumSub,
      2: filterSumUp,
      3: filterSumAvg,
      4: filterSumPaeth
    };
    module2.exports = function(pxData, width, height, options, bpp) {
      let filterTypes;
      if (!("filterType" in options) || options.filterType === -1) {
        filterTypes = [0, 1, 2, 3, 4];
      } else if (typeof options.filterType === "number") {
        filterTypes = [options.filterType];
      } else {
        throw new Error("unrecognised filter types");
      }
      if (options.bitDepth === 16) {
        bpp *= 2;
      }
      let byteWidth = width * bpp;
      let rawPos = 0;
      let pxPos = 0;
      let rawData = Buffer.alloc((byteWidth + 1) * height);
      let sel = filterTypes[0];
      for (let y = 0; y < height; y++) {
        if (filterTypes.length > 1) {
          let min = Infinity;
          for (let i = 0; i < filterTypes.length; i++) {
            let sum = filterSums[filterTypes[i]](pxData, pxPos, byteWidth, bpp);
            if (sum < min) {
              sel = filterTypes[i];
              min = sum;
            }
          }
        }
        rawData[rawPos] = sel;
        rawPos++;
        filters[sel](pxData, pxPos, byteWidth, rawData, rawPos, bpp);
        rawPos += byteWidth;
        pxPos += byteWidth;
      }
      return rawData;
    };
  }
});

// node_modules/pngjs/lib/packer.js
var require_packer = __commonJS({
  "node_modules/pngjs/lib/packer.js"(exports2, module2) {
    "use strict";
    var constants = require_constants();
    var CrcStream = require_crc();
    var bitPacker = require_bitpacker();
    var filter = require_filter_pack();
    var zlib = require("zlib");
    var Packer = module2.exports = function(options) {
      this._options = options;
      options.deflateChunkSize = options.deflateChunkSize || 32 * 1024;
      options.deflateLevel = options.deflateLevel != null ? options.deflateLevel : 9;
      options.deflateStrategy = options.deflateStrategy != null ? options.deflateStrategy : 3;
      options.inputHasAlpha = options.inputHasAlpha != null ? options.inputHasAlpha : true;
      options.deflateFactory = options.deflateFactory || zlib.createDeflate;
      options.bitDepth = options.bitDepth || 8;
      options.colorType = typeof options.colorType === "number" ? options.colorType : constants.COLORTYPE_COLOR_ALPHA;
      options.inputColorType = typeof options.inputColorType === "number" ? options.inputColorType : constants.COLORTYPE_COLOR_ALPHA;
      if ([
        constants.COLORTYPE_GRAYSCALE,
        constants.COLORTYPE_COLOR,
        constants.COLORTYPE_COLOR_ALPHA,
        constants.COLORTYPE_ALPHA
      ].indexOf(options.colorType) === -1) {
        throw new Error(
          "option color type:" + options.colorType + " is not supported at present"
        );
      }
      if ([
        constants.COLORTYPE_GRAYSCALE,
        constants.COLORTYPE_COLOR,
        constants.COLORTYPE_COLOR_ALPHA,
        constants.COLORTYPE_ALPHA
      ].indexOf(options.inputColorType) === -1) {
        throw new Error(
          "option input color type:" + options.inputColorType + " is not supported at present"
        );
      }
      if (options.bitDepth !== 8 && options.bitDepth !== 16) {
        throw new Error(
          "option bit depth:" + options.bitDepth + " is not supported at present"
        );
      }
    };
    Packer.prototype.getDeflateOptions = function() {
      return {
        chunkSize: this._options.deflateChunkSize,
        level: this._options.deflateLevel,
        strategy: this._options.deflateStrategy
      };
    };
    Packer.prototype.createDeflate = function() {
      return this._options.deflateFactory(this.getDeflateOptions());
    };
    Packer.prototype.filterData = function(data, width, height) {
      let packedData = bitPacker(data, width, height, this._options);
      let bpp = constants.COLORTYPE_TO_BPP_MAP[this._options.colorType];
      let filteredData = filter(packedData, width, height, this._options, bpp);
      return filteredData;
    };
    Packer.prototype._packChunk = function(type, data) {
      let len = data ? data.length : 0;
      let buf = Buffer.alloc(len + 12);
      buf.writeUInt32BE(len, 0);
      buf.writeUInt32BE(type, 4);
      if (data) {
        data.copy(buf, 8);
      }
      buf.writeInt32BE(
        CrcStream.crc32(buf.slice(4, buf.length - 4)),
        buf.length - 4
      );
      return buf;
    };
    Packer.prototype.packGAMA = function(gamma) {
      let buf = Buffer.alloc(4);
      buf.writeUInt32BE(Math.floor(gamma * constants.GAMMA_DIVISION), 0);
      return this._packChunk(constants.TYPE_gAMA, buf);
    };
    Packer.prototype.packIHDR = function(width, height) {
      let buf = Buffer.alloc(13);
      buf.writeUInt32BE(width, 0);
      buf.writeUInt32BE(height, 4);
      buf[8] = this._options.bitDepth;
      buf[9] = this._options.colorType;
      buf[10] = 0;
      buf[11] = 0;
      buf[12] = 0;
      return this._packChunk(constants.TYPE_IHDR, buf);
    };
    Packer.prototype.packIDAT = function(data) {
      return this._packChunk(constants.TYPE_IDAT, data);
    };
    Packer.prototype.packIEND = function() {
      return this._packChunk(constants.TYPE_IEND, null);
    };
  }
});

// node_modules/pngjs/lib/packer-async.js
var require_packer_async = __commonJS({
  "node_modules/pngjs/lib/packer-async.js"(exports2, module2) {
    "use strict";
    var util = require("util");
    var Stream = require("stream");
    var constants = require_constants();
    var Packer = require_packer();
    var PackerAsync = module2.exports = function(opt) {
      Stream.call(this);
      let options = opt || {};
      this._packer = new Packer(options);
      this._deflate = this._packer.createDeflate();
      this.readable = true;
    };
    util.inherits(PackerAsync, Stream);
    PackerAsync.prototype.pack = function(data, width, height, gamma) {
      this.emit("data", Buffer.from(constants.PNG_SIGNATURE));
      this.emit("data", this._packer.packIHDR(width, height));
      if (gamma) {
        this.emit("data", this._packer.packGAMA(gamma));
      }
      let filteredData = this._packer.filterData(data, width, height);
      this._deflate.on("error", this.emit.bind(this, "error"));
      this._deflate.on(
        "data",
        function(compressedData) {
          this.emit("data", this._packer.packIDAT(compressedData));
        }.bind(this)
      );
      this._deflate.on(
        "end",
        function() {
          this.emit("data", this._packer.packIEND());
          this.emit("end");
        }.bind(this)
      );
      this._deflate.end(filteredData);
    };
  }
});

// node_modules/pngjs/lib/sync-inflate.js
var require_sync_inflate = __commonJS({
  "node_modules/pngjs/lib/sync-inflate.js"(exports2, module2) {
    "use strict";
    var assert = require("assert").ok;
    var zlib = require("zlib");
    var util = require("util");
    var kMaxLength = require("buffer").kMaxLength;
    function Inflate(opts) {
      if (!(this instanceof Inflate)) {
        return new Inflate(opts);
      }
      if (opts && opts.chunkSize < zlib.Z_MIN_CHUNK) {
        opts.chunkSize = zlib.Z_MIN_CHUNK;
      }
      zlib.Inflate.call(this, opts);
      this._offset = this._offset === void 0 ? this._outOffset : this._offset;
      this._buffer = this._buffer || this._outBuffer;
      if (opts && opts.maxLength != null) {
        this._maxLength = opts.maxLength;
      }
    }
    function createInflate(opts) {
      return new Inflate(opts);
    }
    function _close(engine, callback) {
      if (callback) {
        process.nextTick(callback);
      }
      if (!engine._handle) {
        return;
      }
      engine._handle.close();
      engine._handle = null;
    }
    Inflate.prototype._processChunk = function(chunk, flushFlag, asyncCb) {
      if (typeof asyncCb === "function") {
        return zlib.Inflate._processChunk.call(this, chunk, flushFlag, asyncCb);
      }
      let self = this;
      let availInBefore = chunk && chunk.length;
      let availOutBefore = this._chunkSize - this._offset;
      let leftToInflate = this._maxLength;
      let inOff = 0;
      let buffers = [];
      let nread = 0;
      let error;
      this.on("error", function(err) {
        error = err;
      });
      function handleChunk(availInAfter, availOutAfter) {
        if (self._hadError) {
          return;
        }
        let have = availOutBefore - availOutAfter;
        assert(have >= 0, "have should not go down");
        if (have > 0) {
          let out = self._buffer.slice(self._offset, self._offset + have);
          self._offset += have;
          if (out.length > leftToInflate) {
            out = out.slice(0, leftToInflate);
          }
          buffers.push(out);
          nread += out.length;
          leftToInflate -= out.length;
          if (leftToInflate === 0) {
            return false;
          }
        }
        if (availOutAfter === 0 || self._offset >= self._chunkSize) {
          availOutBefore = self._chunkSize;
          self._offset = 0;
          self._buffer = Buffer.allocUnsafe(self._chunkSize);
        }
        if (availOutAfter === 0) {
          inOff += availInBefore - availInAfter;
          availInBefore = availInAfter;
          return true;
        }
        return false;
      }
      assert(this._handle, "zlib binding closed");
      let res;
      do {
        res = this._handle.writeSync(
          flushFlag,
          chunk,
          // in
          inOff,
          // in_off
          availInBefore,
          // in_len
          this._buffer,
          // out
          this._offset,
          //out_off
          availOutBefore
        );
        res = res || this._writeState;
      } while (!this._hadError && handleChunk(res[0], res[1]));
      if (this._hadError) {
        throw error;
      }
      if (nread >= kMaxLength) {
        _close(this);
        throw new RangeError(
          "Cannot create final Buffer. It would be larger than 0x" + kMaxLength.toString(16) + " bytes"
        );
      }
      let buf = Buffer.concat(buffers, nread);
      _close(this);
      return buf;
    };
    util.inherits(Inflate, zlib.Inflate);
    function zlibBufferSync(engine, buffer) {
      if (typeof buffer === "string") {
        buffer = Buffer.from(buffer);
      }
      if (!(buffer instanceof Buffer)) {
        throw new TypeError("Not a string or buffer");
      }
      let flushFlag = engine._finishFlushFlag;
      if (flushFlag == null) {
        flushFlag = zlib.Z_FINISH;
      }
      return engine._processChunk(buffer, flushFlag);
    }
    function inflateSync(buffer, opts) {
      return zlibBufferSync(new Inflate(opts), buffer);
    }
    module2.exports = exports2 = inflateSync;
    exports2.Inflate = Inflate;
    exports2.createInflate = createInflate;
    exports2.inflateSync = inflateSync;
  }
});

// node_modules/pngjs/lib/sync-reader.js
var require_sync_reader = __commonJS({
  "node_modules/pngjs/lib/sync-reader.js"(exports2, module2) {
    "use strict";
    var SyncReader = module2.exports = function(buffer) {
      this._buffer = buffer;
      this._reads = [];
    };
    SyncReader.prototype.read = function(length, callback) {
      this._reads.push({
        length: Math.abs(length),
        // if length < 0 then at most this length
        allowLess: length < 0,
        func: callback
      });
    };
    SyncReader.prototype.process = function() {
      while (this._reads.length > 0 && this._buffer.length) {
        let read = this._reads[0];
        if (this._buffer.length && (this._buffer.length >= read.length || read.allowLess)) {
          this._reads.shift();
          let buf = this._buffer;
          this._buffer = buf.slice(read.length);
          read.func.call(this, buf.slice(0, read.length));
        } else {
          break;
        }
      }
      if (this._reads.length > 0) {
        return new Error("There are some read requests waitng on finished stream");
      }
      if (this._buffer.length > 0) {
        return new Error("unrecognised content at end of stream");
      }
    };
  }
});

// node_modules/pngjs/lib/filter-parse-sync.js
var require_filter_parse_sync = __commonJS({
  "node_modules/pngjs/lib/filter-parse-sync.js"(exports2) {
    "use strict";
    var SyncReader = require_sync_reader();
    var Filter = require_filter_parse();
    exports2.process = function(inBuffer, bitmapInfo) {
      let outBuffers = [];
      let reader = new SyncReader(inBuffer);
      let filter = new Filter(bitmapInfo, {
        read: reader.read.bind(reader),
        write: function(bufferPart) {
          outBuffers.push(bufferPart);
        },
        complete: function() {
        }
      });
      filter.start();
      reader.process();
      return Buffer.concat(outBuffers);
    };
  }
});

// node_modules/pngjs/lib/parser-sync.js
var require_parser_sync = __commonJS({
  "node_modules/pngjs/lib/parser-sync.js"(exports2, module2) {
    "use strict";
    var hasSyncZlib = true;
    var zlib = require("zlib");
    var inflateSync = require_sync_inflate();
    if (!zlib.deflateSync) {
      hasSyncZlib = false;
    }
    var SyncReader = require_sync_reader();
    var FilterSync = require_filter_parse_sync();
    var Parser = require_parser();
    var bitmapper = require_bitmapper();
    var formatNormaliser = require_format_normaliser();
    module2.exports = function(buffer, options) {
      if (!hasSyncZlib) {
        throw new Error(
          "To use the sync capability of this library in old node versions, please pin pngjs to v2.3.0"
        );
      }
      let err;
      function handleError(_err_) {
        err = _err_;
      }
      let metaData;
      function handleMetaData(_metaData_) {
        metaData = _metaData_;
      }
      function handleTransColor(transColor) {
        metaData.transColor = transColor;
      }
      function handlePalette(palette) {
        metaData.palette = palette;
      }
      function handleSimpleTransparency() {
        metaData.alpha = true;
      }
      let gamma;
      function handleGamma(_gamma_) {
        gamma = _gamma_;
      }
      let inflateDataList = [];
      function handleInflateData(inflatedData2) {
        inflateDataList.push(inflatedData2);
      }
      let reader = new SyncReader(buffer);
      let parser = new Parser(options, {
        read: reader.read.bind(reader),
        error: handleError,
        metadata: handleMetaData,
        gamma: handleGamma,
        palette: handlePalette,
        transColor: handleTransColor,
        inflateData: handleInflateData,
        simpleTransparency: handleSimpleTransparency
      });
      parser.start();
      reader.process();
      if (err) {
        throw err;
      }
      let inflateData = Buffer.concat(inflateDataList);
      inflateDataList.length = 0;
      let inflatedData;
      if (metaData.interlace) {
        inflatedData = zlib.inflateSync(inflateData);
      } else {
        let rowSize = (metaData.width * metaData.bpp * metaData.depth + 7 >> 3) + 1;
        let imageSize = rowSize * metaData.height;
        inflatedData = inflateSync(inflateData, {
          chunkSize: imageSize,
          maxLength: imageSize
        });
      }
      inflateData = null;
      if (!inflatedData || !inflatedData.length) {
        throw new Error("bad png - invalid inflate data response");
      }
      let unfilteredData = FilterSync.process(inflatedData, metaData);
      inflateData = null;
      let bitmapData = bitmapper.dataToBitMap(unfilteredData, metaData);
      unfilteredData = null;
      let normalisedBitmapData = formatNormaliser(bitmapData, metaData);
      metaData.data = normalisedBitmapData;
      metaData.gamma = gamma || 0;
      return metaData;
    };
  }
});

// node_modules/pngjs/lib/packer-sync.js
var require_packer_sync = __commonJS({
  "node_modules/pngjs/lib/packer-sync.js"(exports2, module2) {
    "use strict";
    var hasSyncZlib = true;
    var zlib = require("zlib");
    if (!zlib.deflateSync) {
      hasSyncZlib = false;
    }
    var constants = require_constants();
    var Packer = require_packer();
    module2.exports = function(metaData, opt) {
      if (!hasSyncZlib) {
        throw new Error(
          "To use the sync capability of this library in old node versions, please pin pngjs to v2.3.0"
        );
      }
      let options = opt || {};
      let packer = new Packer(options);
      let chunks = [];
      chunks.push(Buffer.from(constants.PNG_SIGNATURE));
      chunks.push(packer.packIHDR(metaData.width, metaData.height));
      if (metaData.gamma) {
        chunks.push(packer.packGAMA(metaData.gamma));
      }
      let filteredData = packer.filterData(
        metaData.data,
        metaData.width,
        metaData.height
      );
      let compressedData = zlib.deflateSync(
        filteredData,
        packer.getDeflateOptions()
      );
      filteredData = null;
      if (!compressedData || !compressedData.length) {
        throw new Error("bad png - invalid compressed data response");
      }
      chunks.push(packer.packIDAT(compressedData));
      chunks.push(packer.packIEND());
      return Buffer.concat(chunks);
    };
  }
});

// node_modules/pngjs/lib/png-sync.js
var require_png_sync = __commonJS({
  "node_modules/pngjs/lib/png-sync.js"(exports2) {
    "use strict";
    var parse = require_parser_sync();
    var pack = require_packer_sync();
    exports2.read = function(buffer, options) {
      return parse(buffer, options || {});
    };
    exports2.write = function(png, options) {
      return pack(png, options);
    };
  }
});

// node_modules/pngjs/lib/png.js
var require_png = __commonJS({
  "node_modules/pngjs/lib/png.js"(exports2) {
    "use strict";
    var util = require("util");
    var Stream = require("stream");
    var Parser = require_parser_async();
    var Packer = require_packer_async();
    var PNGSync = require_png_sync();
    var PNG = exports2.PNG = function(options) {
      Stream.call(this);
      options = options || {};
      this.width = options.width | 0;
      this.height = options.height | 0;
      this.data = this.width > 0 && this.height > 0 ? Buffer.alloc(4 * this.width * this.height) : null;
      if (options.fill && this.data) {
        this.data.fill(0);
      }
      this.gamma = 0;
      this.readable = this.writable = true;
      this._parser = new Parser(options);
      this._parser.on("error", this.emit.bind(this, "error"));
      this._parser.on("close", this._handleClose.bind(this));
      this._parser.on("metadata", this._metadata.bind(this));
      this._parser.on("gamma", this._gamma.bind(this));
      this._parser.on(
        "parsed",
        function(data) {
          this.data = data;
          this.emit("parsed", data);
        }.bind(this)
      );
      this._packer = new Packer(options);
      this._packer.on("data", this.emit.bind(this, "data"));
      this._packer.on("end", this.emit.bind(this, "end"));
      this._parser.on("close", this._handleClose.bind(this));
      this._packer.on("error", this.emit.bind(this, "error"));
    };
    util.inherits(PNG, Stream);
    PNG.sync = PNGSync;
    PNG.prototype.pack = function() {
      if (!this.data || !this.data.length) {
        this.emit("error", "No data provided");
        return this;
      }
      process.nextTick(
        function() {
          this._packer.pack(this.data, this.width, this.height, this.gamma);
        }.bind(this)
      );
      return this;
    };
    PNG.prototype.parse = function(data, callback) {
      if (callback) {
        let onParsed, onError;
        onParsed = function(parsedData) {
          this.removeListener("error", onError);
          this.data = parsedData;
          callback(null, this);
        }.bind(this);
        onError = function(err) {
          this.removeListener("parsed", onParsed);
          callback(err, null);
        }.bind(this);
        this.once("parsed", onParsed);
        this.once("error", onError);
      }
      this.end(data);
      return this;
    };
    PNG.prototype.write = function(data) {
      this._parser.write(data);
      return true;
    };
    PNG.prototype.end = function(data) {
      this._parser.end(data);
    };
    PNG.prototype._metadata = function(metadata) {
      this.width = metadata.width;
      this.height = metadata.height;
      this.emit("metadata", metadata);
    };
    PNG.prototype._gamma = function(gamma) {
      this.gamma = gamma;
    };
    PNG.prototype._handleClose = function() {
      if (!this._parser.writable && !this._packer.readable) {
        this.emit("close");
      }
    };
    PNG.bitblt = function(src, dst, srcX, srcY, width, height, deltaX, deltaY) {
      srcX |= 0;
      srcY |= 0;
      width |= 0;
      height |= 0;
      deltaX |= 0;
      deltaY |= 0;
      if (srcX > src.width || srcY > src.height || srcX + width > src.width || srcY + height > src.height) {
        throw new Error("bitblt reading outside image");
      }
      if (deltaX > dst.width || deltaY > dst.height || deltaX + width > dst.width || deltaY + height > dst.height) {
        throw new Error("bitblt writing outside image");
      }
      for (let y = 0; y < height; y++) {
        src.data.copy(
          dst.data,
          (deltaY + y) * dst.width + deltaX << 2,
          (srcY + y) * src.width + srcX << 2,
          (srcY + y) * src.width + srcX + width << 2
        );
      }
    };
    PNG.prototype.bitblt = function(dst, srcX, srcY, width, height, deltaX, deltaY) {
      PNG.bitblt(this, dst, srcX, srcY, width, height, deltaX, deltaY);
      return this;
    };
    PNG.adjustGamma = function(src) {
      if (src.gamma) {
        for (let y = 0; y < src.height; y++) {
          for (let x = 0; x < src.width; x++) {
            let idx = src.width * y + x << 2;
            for (let i = 0; i < 3; i++) {
              let sample = src.data[idx + i] / 255;
              sample = Math.pow(sample, 1 / 2.2 / src.gamma);
              src.data[idx + i] = Math.round(sample * 255);
            }
          }
        }
        src.gamma = 0;
      }
    };
    PNG.prototype.adjustGamma = function() {
      PNG.adjustGamma(this);
    };
  }
});

// node_modules/qrcode/lib/renderer/utils.js
var require_utils2 = __commonJS({
  "node_modules/qrcode/lib/renderer/utils.js"(exports2) {
    function hex2rgba(hex) {
      if (typeof hex === "number") {
        hex = hex.toString();
      }
      if (typeof hex !== "string") {
        throw new Error("Color should be defined as hex string");
      }
      let hexCode = hex.slice().replace("#", "").split("");
      if (hexCode.length < 3 || hexCode.length === 5 || hexCode.length > 8) {
        throw new Error("Invalid hex color: " + hex);
      }
      if (hexCode.length === 3 || hexCode.length === 4) {
        hexCode = Array.prototype.concat.apply([], hexCode.map(function(c) {
          return [c, c];
        }));
      }
      if (hexCode.length === 6) hexCode.push("F", "F");
      const hexValue = parseInt(hexCode.join(""), 16);
      return {
        r: hexValue >> 24 & 255,
        g: hexValue >> 16 & 255,
        b: hexValue >> 8 & 255,
        a: hexValue & 255,
        hex: "#" + hexCode.slice(0, 6).join("")
      };
    }
    exports2.getOptions = function getOptions(options) {
      if (!options) options = {};
      if (!options.color) options.color = {};
      const margin = typeof options.margin === "undefined" || options.margin === null || options.margin < 0 ? 4 : options.margin;
      const width = options.width && options.width >= 21 ? options.width : void 0;
      const scale = options.scale || 4;
      return {
        width,
        scale: width ? 4 : scale,
        margin,
        color: {
          dark: hex2rgba(options.color.dark || "#000000ff"),
          light: hex2rgba(options.color.light || "#ffffffff")
        },
        type: options.type,
        rendererOpts: options.rendererOpts || {}
      };
    };
    exports2.getScale = function getScale(qrSize, opts) {
      return opts.width && opts.width >= qrSize + opts.margin * 2 ? opts.width / (qrSize + opts.margin * 2) : opts.scale;
    };
    exports2.getImageWidth = function getImageWidth(qrSize, opts) {
      const scale = exports2.getScale(qrSize, opts);
      return Math.floor((qrSize + opts.margin * 2) * scale);
    };
    exports2.qrToImageData = function qrToImageData(imgData, qr, opts) {
      const size = qr.modules.size;
      const data = qr.modules.data;
      const scale = exports2.getScale(size, opts);
      const symbolSize = Math.floor((size + opts.margin * 2) * scale);
      const scaledMargin = opts.margin * scale;
      const palette = [opts.color.light, opts.color.dark];
      for (let i = 0; i < symbolSize; i++) {
        for (let j = 0; j < symbolSize; j++) {
          let posDst = (i * symbolSize + j) * 4;
          let pxColor = opts.color.light;
          if (i >= scaledMargin && j >= scaledMargin && i < symbolSize - scaledMargin && j < symbolSize - scaledMargin) {
            const iSrc = Math.floor((i - scaledMargin) / scale);
            const jSrc = Math.floor((j - scaledMargin) / scale);
            pxColor = palette[data[iSrc * size + jSrc] ? 1 : 0];
          }
          imgData[posDst++] = pxColor.r;
          imgData[posDst++] = pxColor.g;
          imgData[posDst++] = pxColor.b;
          imgData[posDst] = pxColor.a;
        }
      }
    };
  }
});

// node_modules/qrcode/lib/renderer/png.js
var require_png2 = __commonJS({
  "node_modules/qrcode/lib/renderer/png.js"(exports2) {
    var fs2 = require("fs");
    var PNG = require_png().PNG;
    var Utils = require_utils2();
    exports2.render = function render(qrData, options) {
      const opts = Utils.getOptions(options);
      const pngOpts = opts.rendererOpts;
      const size = Utils.getImageWidth(qrData.modules.size, opts);
      pngOpts.width = size;
      pngOpts.height = size;
      const pngImage = new PNG(pngOpts);
      Utils.qrToImageData(pngImage.data, qrData, opts);
      return pngImage;
    };
    exports2.renderToDataURL = function renderToDataURL(qrData, options, cb) {
      if (typeof cb === "undefined") {
        cb = options;
        options = void 0;
      }
      exports2.renderToBuffer(qrData, options, function(err, output) {
        if (err) cb(err);
        let url = "data:image/png;base64,";
        url += output.toString("base64");
        cb(null, url);
      });
    };
    exports2.renderToBuffer = function renderToBuffer(qrData, options, cb) {
      if (typeof cb === "undefined") {
        cb = options;
        options = void 0;
      }
      const png = exports2.render(qrData, options);
      const buffer = [];
      png.on("error", cb);
      png.on("data", function(data) {
        buffer.push(data);
      });
      png.on("end", function() {
        cb(null, Buffer.concat(buffer));
      });
      png.pack();
    };
    exports2.renderToFile = function renderToFile(path2, qrData, options, cb) {
      if (typeof cb === "undefined") {
        cb = options;
        options = void 0;
      }
      let called = false;
      const done = (...args) => {
        if (called) return;
        called = true;
        cb.apply(null, args);
      };
      const stream = fs2.createWriteStream(path2);
      stream.on("error", done);
      stream.on("close", done);
      exports2.renderToFileStream(stream, qrData, options);
    };
    exports2.renderToFileStream = function renderToFileStream(stream, qrData, options) {
      const png = exports2.render(qrData, options);
      png.pack().pipe(stream);
    };
  }
});

// node_modules/qrcode/lib/renderer/utf8.js
var require_utf8 = __commonJS({
  "node_modules/qrcode/lib/renderer/utf8.js"(exports2) {
    var Utils = require_utils2();
    var BLOCK_CHAR = {
      WW: " ",
      WB: "\u2584",
      BB: "\u2588",
      BW: "\u2580"
    };
    var INVERTED_BLOCK_CHAR = {
      BB: " ",
      BW: "\u2584",
      WW: "\u2588",
      WB: "\u2580"
    };
    function getBlockChar(top, bottom, blocks) {
      if (top && bottom) return blocks.BB;
      if (top && !bottom) return blocks.BW;
      if (!top && bottom) return blocks.WB;
      return blocks.WW;
    }
    exports2.render = function(qrData, options, cb) {
      const opts = Utils.getOptions(options);
      let blocks = BLOCK_CHAR;
      if (opts.color.dark.hex === "#ffffff" || opts.color.light.hex === "#000000") {
        blocks = INVERTED_BLOCK_CHAR;
      }
      const size = qrData.modules.size;
      const data = qrData.modules.data;
      let output = "";
      let hMargin = Array(size + opts.margin * 2 + 1).join(blocks.WW);
      hMargin = Array(opts.margin / 2 + 1).join(hMargin + "\n");
      const vMargin = Array(opts.margin + 1).join(blocks.WW);
      output += hMargin;
      for (let i = 0; i < size; i += 2) {
        output += vMargin;
        for (let j = 0; j < size; j++) {
          const topModule = data[i * size + j];
          const bottomModule = data[(i + 1) * size + j];
          output += getBlockChar(topModule, bottomModule, blocks);
        }
        output += vMargin + "\n";
      }
      output += hMargin.slice(0, -1);
      if (typeof cb === "function") {
        cb(null, output);
      }
      return output;
    };
    exports2.renderToFile = function renderToFile(path2, qrData, options, cb) {
      if (typeof cb === "undefined") {
        cb = options;
        options = void 0;
      }
      const fs2 = require("fs");
      const utf8 = exports2.render(qrData, options);
      fs2.writeFile(path2, utf8, cb);
    };
  }
});

// node_modules/qrcode/lib/renderer/terminal/terminal.js
var require_terminal = __commonJS({
  "node_modules/qrcode/lib/renderer/terminal/terminal.js"(exports2) {
    exports2.render = function(qrData, options, cb) {
      const size = qrData.modules.size;
      const data = qrData.modules.data;
      const black = "\x1B[40m  \x1B[0m";
      const white = "\x1B[47m  \x1B[0m";
      let output = "";
      const hMargin = Array(size + 3).join(white);
      const vMargin = Array(2).join(white);
      output += hMargin + "\n";
      for (let i = 0; i < size; ++i) {
        output += white;
        for (let j = 0; j < size; j++) {
          output += data[i * size + j] ? black : white;
        }
        output += vMargin + "\n";
      }
      output += hMargin + "\n";
      if (typeof cb === "function") {
        cb(null, output);
      }
      return output;
    };
  }
});

// node_modules/qrcode/lib/renderer/terminal/terminal-small.js
var require_terminal_small = __commonJS({
  "node_modules/qrcode/lib/renderer/terminal/terminal-small.js"(exports2) {
    var backgroundWhite = "\x1B[47m";
    var backgroundBlack = "\x1B[40m";
    var foregroundWhite = "\x1B[37m";
    var foregroundBlack = "\x1B[30m";
    var reset = "\x1B[0m";
    var lineSetupNormal = backgroundWhite + foregroundBlack;
    var lineSetupInverse = backgroundBlack + foregroundWhite;
    var createPalette = function(lineSetup, foregroundWhite2, foregroundBlack2) {
      return {
        // 1 ... white, 2 ... black, 0 ... transparent (default)
        "00": reset + " " + lineSetup,
        "01": reset + foregroundWhite2 + "\u2584" + lineSetup,
        "02": reset + foregroundBlack2 + "\u2584" + lineSetup,
        10: reset + foregroundWhite2 + "\u2580" + lineSetup,
        11: " ",
        12: "\u2584",
        20: reset + foregroundBlack2 + "\u2580" + lineSetup,
        21: "\u2580",
        22: "\u2588"
      };
    };
    var mkCodePixel = function(modules, size, x, y) {
      const sizePlus = size + 1;
      if (x >= sizePlus || y >= sizePlus || y < -1 || x < -1) return "0";
      if (x >= size || y >= size || y < 0 || x < 0) return "1";
      const idx = y * size + x;
      return modules[idx] ? "2" : "1";
    };
    var mkCode = function(modules, size, x, y) {
      return mkCodePixel(modules, size, x, y) + mkCodePixel(modules, size, x, y + 1);
    };
    exports2.render = function(qrData, options, cb) {
      const size = qrData.modules.size;
      const data = qrData.modules.data;
      const inverse = !!(options && options.inverse);
      const lineSetup = options && options.inverse ? lineSetupInverse : lineSetupNormal;
      const white = inverse ? foregroundBlack : foregroundWhite;
      const black = inverse ? foregroundWhite : foregroundBlack;
      const palette = createPalette(lineSetup, white, black);
      const newLine = reset + "\n" + lineSetup;
      let output = lineSetup;
      for (let y = -1; y < size + 1; y += 2) {
        for (let x = -1; x < size; x++) {
          output += palette[mkCode(data, size, x, y)];
        }
        output += palette[mkCode(data, size, size, y)] + newLine;
      }
      output += reset;
      if (typeof cb === "function") {
        cb(null, output);
      }
      return output;
    };
  }
});

// node_modules/qrcode/lib/renderer/terminal.js
var require_terminal2 = __commonJS({
  "node_modules/qrcode/lib/renderer/terminal.js"(exports2) {
    var big = require_terminal();
    var small = require_terminal_small();
    exports2.render = function(qrData, options, cb) {
      if (options && options.small) {
        return small.render(qrData, options, cb);
      }
      return big.render(qrData, options, cb);
    };
  }
});

// node_modules/qrcode/lib/renderer/svg-tag.js
var require_svg_tag = __commonJS({
  "node_modules/qrcode/lib/renderer/svg-tag.js"(exports2) {
    var Utils = require_utils2();
    function getColorAttrib(color, attrib) {
      const alpha = color.a / 255;
      const str = attrib + '="' + color.hex + '"';
      return alpha < 1 ? str + " " + attrib + '-opacity="' + alpha.toFixed(2).slice(1) + '"' : str;
    }
    function svgCmd(cmd, x, y) {
      let str = cmd + x;
      if (typeof y !== "undefined") str += " " + y;
      return str;
    }
    function qrToPath(data, size, margin) {
      let path2 = "";
      let moveBy = 0;
      let newRow = false;
      let lineLength = 0;
      for (let i = 0; i < data.length; i++) {
        const col = Math.floor(i % size);
        const row = Math.floor(i / size);
        if (!col && !newRow) newRow = true;
        if (data[i]) {
          lineLength++;
          if (!(i > 0 && col > 0 && data[i - 1])) {
            path2 += newRow ? svgCmd("M", col + margin, 0.5 + row + margin) : svgCmd("m", moveBy, 0);
            moveBy = 0;
            newRow = false;
          }
          if (!(col + 1 < size && data[i + 1])) {
            path2 += svgCmd("h", lineLength);
            lineLength = 0;
          }
        } else {
          moveBy++;
        }
      }
      return path2;
    }
    exports2.render = function render(qrData, options, cb) {
      const opts = Utils.getOptions(options);
      const size = qrData.modules.size;
      const data = qrData.modules.data;
      const qrcodesize = size + opts.margin * 2;
      const bg = !opts.color.light.a ? "" : "<path " + getColorAttrib(opts.color.light, "fill") + ' d="M0 0h' + qrcodesize + "v" + qrcodesize + 'H0z"/>';
      const path2 = "<path " + getColorAttrib(opts.color.dark, "stroke") + ' d="' + qrToPath(data, size, opts.margin) + '"/>';
      const viewBox = 'viewBox="0 0 ' + qrcodesize + " " + qrcodesize + '"';
      const width = !opts.width ? "" : 'width="' + opts.width + '" height="' + opts.width + '" ';
      const svgTag = '<svg xmlns="http://www.w3.org/2000/svg" ' + width + viewBox + ' shape-rendering="crispEdges">' + bg + path2 + "</svg>\n";
      if (typeof cb === "function") {
        cb(null, svgTag);
      }
      return svgTag;
    };
  }
});

// node_modules/qrcode/lib/renderer/svg.js
var require_svg = __commonJS({
  "node_modules/qrcode/lib/renderer/svg.js"(exports2) {
    var svgTagRenderer = require_svg_tag();
    exports2.render = svgTagRenderer.render;
    exports2.renderToFile = function renderToFile(path2, qrData, options, cb) {
      if (typeof cb === "undefined") {
        cb = options;
        options = void 0;
      }
      const fs2 = require("fs");
      const svgTag = exports2.render(qrData, options);
      const xmlStr = '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + svgTag;
      fs2.writeFile(path2, xmlStr, cb);
    };
  }
});

// node_modules/qrcode/lib/renderer/canvas.js
var require_canvas = __commonJS({
  "node_modules/qrcode/lib/renderer/canvas.js"(exports2) {
    var Utils = require_utils2();
    function clearCanvas(ctx, canvas, size) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!canvas.style) canvas.style = {};
      canvas.height = size;
      canvas.width = size;
      canvas.style.height = size + "px";
      canvas.style.width = size + "px";
    }
    function getCanvasElement() {
      try {
        return document.createElement("canvas");
      } catch (e) {
        throw new Error("You need to specify a canvas element");
      }
    }
    exports2.render = function render(qrData, canvas, options) {
      let opts = options;
      let canvasEl = canvas;
      if (typeof opts === "undefined" && (!canvas || !canvas.getContext)) {
        opts = canvas;
        canvas = void 0;
      }
      if (!canvas) {
        canvasEl = getCanvasElement();
      }
      opts = Utils.getOptions(opts);
      const size = Utils.getImageWidth(qrData.modules.size, opts);
      const ctx = canvasEl.getContext("2d");
      const image = ctx.createImageData(size, size);
      Utils.qrToImageData(image.data, qrData, opts);
      clearCanvas(ctx, canvasEl, size);
      ctx.putImageData(image, 0, 0);
      return canvasEl;
    };
    exports2.renderToDataURL = function renderToDataURL(qrData, canvas, options) {
      let opts = options;
      if (typeof opts === "undefined" && (!canvas || !canvas.getContext)) {
        opts = canvas;
        canvas = void 0;
      }
      if (!opts) opts = {};
      const canvasEl = exports2.render(qrData, canvas, opts);
      const type = opts.type || "image/png";
      const rendererOpts = opts.rendererOpts || {};
      return canvasEl.toDataURL(type, rendererOpts.quality);
    };
  }
});

// node_modules/qrcode/lib/browser.js
var require_browser = __commonJS({
  "node_modules/qrcode/lib/browser.js"(exports2) {
    var canPromise = require_can_promise();
    var QRCode = require_qrcode();
    var CanvasRenderer = require_canvas();
    var SvgRenderer = require_svg_tag();
    function renderCanvas(renderFunc, canvas, text, opts, cb) {
      const args = [].slice.call(arguments, 1);
      const argsNum = args.length;
      const isLastArgCb = typeof args[argsNum - 1] === "function";
      if (!isLastArgCb && !canPromise()) {
        throw new Error("Callback required as last argument");
      }
      if (isLastArgCb) {
        if (argsNum < 2) {
          throw new Error("Too few arguments provided");
        }
        if (argsNum === 2) {
          cb = text;
          text = canvas;
          canvas = opts = void 0;
        } else if (argsNum === 3) {
          if (canvas.getContext && typeof cb === "undefined") {
            cb = opts;
            opts = void 0;
          } else {
            cb = opts;
            opts = text;
            text = canvas;
            canvas = void 0;
          }
        }
      } else {
        if (argsNum < 1) {
          throw new Error("Too few arguments provided");
        }
        if (argsNum === 1) {
          text = canvas;
          canvas = opts = void 0;
        } else if (argsNum === 2 && !canvas.getContext) {
          opts = text;
          text = canvas;
          canvas = void 0;
        }
        return new Promise(function(resolve3, reject) {
          try {
            const data = QRCode.create(text, opts);
            resolve3(renderFunc(data, canvas, opts));
          } catch (e) {
            reject(e);
          }
        });
      }
      try {
        const data = QRCode.create(text, opts);
        cb(null, renderFunc(data, canvas, opts));
      } catch (e) {
        cb(e);
      }
    }
    exports2.create = QRCode.create;
    exports2.toCanvas = renderCanvas.bind(null, CanvasRenderer.render);
    exports2.toDataURL = renderCanvas.bind(null, CanvasRenderer.renderToDataURL);
    exports2.toString = renderCanvas.bind(null, function(data, _, opts) {
      return SvgRenderer.render(data, opts);
    });
  }
});

// node_modules/qrcode/lib/server.js
var require_server = __commonJS({
  "node_modules/qrcode/lib/server.js"(exports2) {
    var canPromise = require_can_promise();
    var QRCode = require_qrcode();
    var PngRenderer = require_png2();
    var Utf8Renderer = require_utf8();
    var TerminalRenderer = require_terminal2();
    var SvgRenderer = require_svg();
    function checkParams(text, opts, cb) {
      if (typeof text === "undefined") {
        throw new Error("String required as first argument");
      }
      if (typeof cb === "undefined") {
        cb = opts;
        opts = {};
      }
      if (typeof cb !== "function") {
        if (!canPromise()) {
          throw new Error("Callback required as last argument");
        } else {
          opts = cb || {};
          cb = null;
        }
      }
      return {
        opts,
        cb
      };
    }
    function getTypeFromFilename(path2) {
      return path2.slice((path2.lastIndexOf(".") - 1 >>> 0) + 2).toLowerCase();
    }
    function getRendererFromType(type) {
      switch (type) {
        case "svg":
          return SvgRenderer;
        case "txt":
        case "utf8":
          return Utf8Renderer;
        case "png":
        case "image/png":
        default:
          return PngRenderer;
      }
    }
    function getStringRendererFromType(type) {
      switch (type) {
        case "svg":
          return SvgRenderer;
        case "terminal":
          return TerminalRenderer;
        case "utf8":
        default:
          return Utf8Renderer;
      }
    }
    function render(renderFunc, text, params) {
      if (!params.cb) {
        return new Promise(function(resolve3, reject) {
          try {
            const data = QRCode.create(text, params.opts);
            return renderFunc(data, params.opts, function(err, data2) {
              return err ? reject(err) : resolve3(data2);
            });
          } catch (e) {
            reject(e);
          }
        });
      }
      try {
        const data = QRCode.create(text, params.opts);
        return renderFunc(data, params.opts, params.cb);
      } catch (e) {
        params.cb(e);
      }
    }
    exports2.create = QRCode.create;
    exports2.toCanvas = require_browser().toCanvas;
    exports2.toString = function toString(text, opts, cb) {
      const params = checkParams(text, opts, cb);
      const type = params.opts ? params.opts.type : void 0;
      const renderer = getStringRendererFromType(type);
      return render(renderer.render, text, params);
    };
    exports2.toDataURL = function toDataURL(text, opts, cb) {
      const params = checkParams(text, opts, cb);
      const renderer = getRendererFromType(params.opts.type);
      return render(renderer.renderToDataURL, text, params);
    };
    exports2.toBuffer = function toBuffer(text, opts, cb) {
      const params = checkParams(text, opts, cb);
      const renderer = getRendererFromType(params.opts.type);
      return render(renderer.renderToBuffer, text, params);
    };
    exports2.toFile = function toFile(path2, text, opts, cb) {
      if (typeof path2 !== "string" || !(typeof text === "string" || typeof text === "object")) {
        throw new Error("Invalid argument");
      }
      if (arguments.length < 3 && !canPromise()) {
        throw new Error("Too few arguments provided");
      }
      const params = checkParams(text, opts, cb);
      const type = params.opts.type || getTypeFromFilename(path2);
      const renderer = getRendererFromType(type);
      const renderToFile = renderer.renderToFile.bind(null, path2);
      return render(renderToFile, text, params);
    };
    exports2.toFileStream = function toFileStream(stream, text, opts) {
      if (arguments.length < 2) {
        throw new Error("Too few arguments provided");
      }
      const params = checkParams(text, opts, stream.emit.bind(stream, "error"));
      const renderer = getRendererFromType("png");
      const renderToFileStream = renderer.renderToFileStream.bind(null, stream);
      render(renderToFileStream, text, params);
    };
  }
});

// node_modules/qrcode/lib/index.js
var require_lib = __commonJS({
  "node_modules/qrcode/lib/index.js"(exports2, module2) {
    module2.exports = require_server();
  }
});

// electron/backend/wechat_bridge/src/main.ts
var main_exports = {};
__export(main_exports, {
  createDaemonRuntime: () => createDaemonRuntime,
  flushPendingSends: () => flushPendingSends,
  getLastTurnInfo: () => getLastTurnInfo,
  lastTurnInfo: () => lastTurnInfo,
  pendingProactiveSends: () => pendingProactiveSends,
  queueProactiveSend: () => queueProactiveSend,
  setAbcSessionHelper: () => setAbcSessionHelper,
  setInboundInterceptor: () => setInboundInterceptor
});
function setAbcSessionHelper(h) {
  abcSessionHelper = h;
}
function setInboundInterceptor(fn) {
  inboundInterceptor = fn;
}
function extractFilePathsFromText(text, cwd) {
  const paths = [];
  const regex = /(?:\/(?:Users|home|tmp|var|etc)\/[^\s`'"()\[\]{}|<>]+\.\w+|~\/[^\s`'"()\[\]{}|<>]+\.\w+|[A-Za-z]:[\\\/][^\s`'"()\[\]{}|<>]+\.\w+)/g;
  let match;
  while ((match = regex.exec(text)) !== null) {
    const raw = match[0];
    const resolved = raw.startsWith("~") ? raw.replace(/^~/, (0, import_node_os5.homedir)()) : raw;
    paths.push(resolved);
  }
  return paths;
}
function parseBlocks(text) {
  return text.split(/\n\n+/).filter((block) => block.length > 0);
}
function findSafeSplitPoint(text, maxLen) {
  let idx = text.lastIndexOf("\n", maxLen);
  if (idx >= maxLen * 0.3) return idx;
  const sentenceEnd = /[。！？.!?]$/;
  for (let i = maxLen; i >= maxLen * 0.5; i--) {
    if (sentenceEnd.test(text.slice(i - 1, i))) return i;
  }
  idx = text.lastIndexOf(" ", maxLen);
  if (idx >= maxLen * 0.3) return idx;
  return maxLen;
}
function splitByNewline(text, maxLen) {
  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= maxLen) {
      chunks.push(remaining);
      break;
    }
    const splitIdx = findSafeSplitPoint(remaining, maxLen);
    chunks.push(remaining.slice(0, splitIdx));
    remaining = remaining.slice(splitIdx).replace(/^\n+/, "");
  }
  return chunks;
}
function splitMessage(text, maxLen = MAX_MESSAGE_LENGTH) {
  if (text.length <= maxLen) return [text];
  const blocks = parseBlocks(text);
  const chunks = [];
  let current = "";
  for (const block of blocks) {
    if (current.length === 0) {
      if (block.length <= maxLen) {
        current = block;
      } else {
        chunks.push(...splitByNewline(block, maxLen));
      }
    } else if (current.length + 2 + block.length <= maxLen) {
      current += "\n\n" + block;
    } else {
      chunks.push(current);
      if (block.length <= maxLen) {
        current = block;
      } else {
        chunks.push(...splitByNewline(block, maxLen));
        current = "";
      }
    }
  }
  if (current) chunks.push(current);
  return chunks;
}
function formatNowForModel(now = /* @__PURE__ */ new Date()) {
  try {
    const fmtDate = new Intl.DateTimeFormat("zh-CN", {
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "long"
    });
    const fmtTime = new Intl.DateTimeFormat("zh-CN", {
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    });
    return `${fmtDate.format(now)} ${fmtTime.format(now)}`;
  } catch {
    return now.toISOString();
  }
}
function promptUser(question, defaultValue) {
  return new Promise((resolve3) => {
    const rl = (0, import_node_readline.createInterface)({ input: import_node_process.default.stdin, output: import_node_process.default.stdout });
    const display = defaultValue ? `${question} [${defaultValue}]: ` : `${question}: `;
    rl.question(display, (answer) => {
      rl.close();
      resolve3(answer.trim() || defaultValue || "");
    });
  });
}
function openFile(filePath) {
  const platform = import_node_process.default.platform;
  let cmd;
  let args;
  if (platform === "darwin") {
    cmd = "open";
    args = [filePath];
  } else if (platform === "win32") {
    cmd = "cmd";
    args = ["/c", "start", "", filePath];
  } else {
    cmd = "xdg-open";
    args = [filePath];
  }
  const result = (0, import_node_child_process.spawnSync)(cmd, args, { stdio: "ignore" });
  if (result.error) {
    logger.warn("Failed to open file", { cmd, filePath, error: result.error.message });
  }
}
async function runSetup() {
  (0, import_node_fs12.mkdirSync)(DATA_DIR, { recursive: true });
  const QR_PATH = (0, import_node_path14.join)(DATA_DIR, "qrcode.png");
  console.log("\u6B63\u5728\u8BBE\u7F6E...\n");
  while (true) {
    const { qrcodeUrl, qrcodeId } = await startQrLogin();
    const isHeadlessLinux = import_node_process.default.platform === "linux" && !import_node_process.default.env.DISPLAY && !import_node_process.default.env.WAYLAND_DISPLAY;
    if (isHeadlessLinux) {
      try {
        const qrcodeTerminal = await import("qrcode-terminal");
        console.log("\u8BF7\u7528\u5FAE\u4FE1\u626B\u63CF\u4E0B\u65B9\u4E8C\u7EF4\u7801\uFF1A\n");
        qrcodeTerminal.default.generate(qrcodeUrl, { small: true });
        console.log();
        console.log("\u4E8C\u7EF4\u7801\u94FE\u63A5\uFF1A", qrcodeUrl);
        console.log();
      } catch {
        logger.warn("qrcode-terminal not available, falling back to URL");
        console.log("\u65E0\u6CD5\u5728\u7EC8\u7AEF\u663E\u793A\u4E8C\u7EF4\u7801\uFF0C\u8BF7\u8BBF\u95EE\u94FE\u63A5\uFF1A");
        console.log(qrcodeUrl);
        console.log();
      }
    } else {
      const QRCode = await Promise.resolve().then(() => __toESM(require_lib()));
      const pngData = await QRCode.toBuffer(qrcodeUrl, { type: "png", width: 400, margin: 2 });
      (0, import_node_fs12.writeFileSync)(QR_PATH, pngData);
      openFile(QR_PATH);
      console.log("\u5DF2\u6253\u5F00\u4E8C\u7EF4\u7801\u56FE\u7247\uFF0C\u8BF7\u7528\u5FAE\u4FE1\u626B\u63CF\uFF1A");
      console.log(`\u56FE\u7247\u8DEF\u5F84: ${QR_PATH}
`);
    }
    console.log("\u7B49\u5F85\u626B\u7801\u7ED1\u5B9A...");
    try {
      await waitForQrScan(qrcodeId);
      console.log("\u2705 \u7ED1\u5B9A\u6210\u529F!");
      break;
    } catch (err) {
      if (err.message?.includes("expired")) {
        console.log("\u26A0\uFE0F \u4E8C\u7EF4\u7801\u5DF2\u8FC7\u671F\uFF0C\u6B63\u5728\u5237\u65B0...\n");
        continue;
      }
      throw err;
    }
  }
  try {
    (0, import_node_fs12.unlinkSync)(QR_PATH);
  } catch {
    logger.warn("Failed to clean up QR image", { path: QR_PATH });
  }
  const workingDir = await promptUser("\u8BF7\u8F93\u5165\u5DE5\u4F5C\u76EE\u5F55", (0, import_node_path14.join)((0, import_node_os5.homedir)(), "Documents", "ClaudeCode"));
  const config = loadConfig();
  config.workingDirectory = workingDir;
  saveConfig(config);
  console.log("\u8FD0\u884C npm run daemon -- start \u542F\u52A8\u670D\u52A1");
}
function getLastTurnInfo() {
  return lastTurnInfo;
}
function loadPendingSends() {
  try {
    const parsed = JSON.parse((0, import_node_fs12.readFileSync)(PENDING_SENDS_FILE, "utf8"));
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    return parsed.filter((x) => x && typeof x.text === "string" && typeof x.ts === "number" && now - x.ts < PENDING_SENDS_TTL_MS).slice(0, PENDING_SENDS_MAX);
  } catch {
    return [];
  }
}
function savePendingSends(list) {
  try {
    (0, import_node_fs12.mkdirSync)(DATA_DIR, { recursive: true });
    (0, import_node_fs12.writeFileSync)(PENDING_SENDS_FILE, JSON.stringify(list), "utf8");
  } catch (err) {
    logger.warn("savePendingSends failed", { error: err instanceof Error ? err.message : String(err) });
  }
}
function queueProactiveSend(text) {
  pendingProactiveSends.push({ text, ts: Date.now() });
  while (pendingProactiveSends.length > PENDING_SENDS_MAX) pendingProactiveSends.shift();
  const now = Date.now();
  while (pendingProactiveSends.length && now - pendingProactiveSends[0].ts > PENDING_SENDS_TTL_MS) {
    pendingProactiveSends.shift();
  }
  savePendingSends(pendingProactiveSends);
}
async function flushPendingSends(sender, freshToken) {
  if (!pendingProactiveSends.length || !freshToken || !lastTurnInfo.userId) return;
  const batch = pendingProactiveSends.splice(0, pendingProactiveSends.length);
  for (let i = 0; i < batch.length; i++) {
    try {
      await sender.sendText(lastTurnInfo.userId, freshToken, batch[i].text);
      logger.info("pending proactive send delivered", { index: i, queuedAt: batch[i].ts });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn("pending proactive send failed, re-queueing remainder", { error: msg, index: i });
      pendingProactiveSends.push(...batch.slice(i));
      break;
    }
  }
  savePendingSends(pendingProactiveSends);
}
async function createDaemonRuntime() {
  const config = loadConfig();
  const account = loadLatestAccount();
  if (!account) {
    logger.warn("createDaemonRuntime: no bound account");
    return null;
  }
  const api = new WeChatApi(account.botToken, account.baseUrl);
  const sessionStore = createSessionStore();
  const session = sessionStore.load(account.accountId);
  if (config.workingDirectory && session.workingDirectory === import_node_process.default.cwd()) {
    session.workingDirectory = config.workingDirectory;
    sessionStore.save(account.accountId, session);
  }
  if (session.state !== "idle") {
    logger.warn("Resetting stale session state on startup", { state: session.state });
    session.state = "idle";
    sessionStore.save(account.accountId, session);
  }
  const sender = createSender(api, account.accountId);
  const sharedCtx = { lastContextToken: "" };
  const activeControllers = /* @__PURE__ */ new Map();
  const messageQueue = [];
  let processingQueue = false;
  async function drainQueue() {
    if (processingQueue) return;
    processingQueue = true;
    while (messageQueue.length > 0) {
      const msg = messageQueue.shift();
      await handleMessage(msg, account, session, sessionStore, sender, config, sharedCtx, activeControllers, messageQueue);
    }
    processingQueue = false;
  }
  function handlePriorityCommand(msg) {
    if (msg.message_type !== 1 /* USER */ || !msg.item_list) return false;
    const text = extractTextFromItems(msg.item_list);
    if (!text.startsWith("/stop") && !text.startsWith("/clear")) return false;
    if (session.state !== "processing") return false;
    const ctrl = activeControllers.get(account.accountId);
    if (ctrl) {
      ctrl.abort();
      activeControllers.delete(account.accountId);
    }
    session.state = "idle";
    sessionStore.save(account.accountId, session);
    if (text.startsWith("/stop")) {
      messageQueue.length = 0;
      sender.sendText(msg.from_user_id, msg.context_token ?? "", "\u23F9 \u5DF2\u505C\u6B62\u5F53\u524D\u5BF9\u8BDD\uFF0C\u6392\u961F\u4E2D\u7684\u6D88\u606F\u5DF2\u6E05\u7A7A\u3002").catch(() => {
      });
    }
    return true;
  }
  const callbacks = {
    onMessage: async (msg) => {
      if (msg.message_type === 1 /* USER */) {
        lastTurnInfo.contextToken = msg.context_token ?? lastTurnInfo.contextToken;
        if (msg.from_user_id) lastTurnInfo.userId = msg.from_user_id;
        const freshToken = msg.context_token ?? lastTurnInfo.contextToken;
        if (pendingProactiveSends.length && freshToken) {
          flushPendingSends(sender, freshToken).catch((err) => {
            logger.warn("flushPendingSends threw", { error: err instanceof Error ? err.message : String(err) });
          });
        }
        if (msg.item_list && inboundInterceptor) {
          const interceptText = extractTextFromItems(msg.item_list);
          if (interceptText && inboundInterceptor(interceptText, msg.from_user_id ?? "")) {
            logger.info("Inbound message consumed by approval interceptor", {
              fromUserId: msg.from_user_id,
              text: interceptText.slice(0, 40)
            });
            return;
          }
        }
      }
      if (handlePriorityCommand(msg)) return;
      messageQueue.push(msg);
      drainQueue();
    },
    onSessionExpired: () => {
      logger.warn("Session expired, will keep retrying...");
      console.error("\u26A0\uFE0F \u5FAE\u4FE1\u4F1A\u8BDD\u5DF2\u8FC7\u671F\uFF0C\u8BF7\u91CD\u65B0\u8FD0\u884C setup \u626B\u7801\u7ED1\u5B9A");
    }
  };
  const monitor = createMonitor(api, callbacks);
  let stopped = false;
  const runPromise = monitor.run().catch((err) => {
    logger.error("Monitor crashed", { error: err instanceof Error ? err.message : String(err) });
  });
  logger.info("Daemon runtime started", { accountId: account.accountId });
  console.log(`\u5DF2\u542F\u52A8 (\u8D26\u53F7: ${account.accountId})`);
  return {
    accountId: account.accountId,
    api,
    sender,
    session,
    stop() {
      if (stopped) return;
      stopped = true;
      logger.info("Daemon runtime stopping...");
      monitor.stop();
    }
  };
}
async function runDaemon() {
  const runtime2 = await createDaemonRuntime();
  if (!runtime2) {
    console.error("\u672A\u627E\u5230\u8D26\u53F7\uFF0C\u8BF7\u5148\u8FD0\u884C node dist/main.js setup");
    import_node_process.default.exit(1);
  }
  function shutdown() {
    logger.info("Shutting down...");
    runtime2.stop();
    import_node_process.default.exit(0);
  }
  import_node_process.default.on("SIGINT", shutdown);
  import_node_process.default.on("SIGTERM", shutdown);
  await new Promise((resolve3) => {
    const check = setInterval(() => {
      void check;
    }, 6e4);
    runtime2.stop = new Proxy(runtime2.stop, {
      apply(target, thisArg, args) {
        clearInterval(check);
        resolve3();
        return Reflect.apply(target, thisArg, args);
      }
    });
  });
  runtime2.stop();
}
async function handleMessage(msg, account, session, sessionStore, sender, config, sharedCtx, activeControllers, messageQueue) {
  if (msg.message_type !== 1 /* USER */) return;
  if (!msg.from_user_id || !msg.item_list) return;
  if (account.userId && msg.from_user_id !== account.userId) return;
  const contextToken = msg.context_token ?? "";
  const fromUserId = msg.from_user_id;
  sharedCtx.lastContextToken = contextToken;
  let abcSessionId = null;
  if (abcSessionHelper && fromUserId) {
    try {
      const masked = fromUserId.length > 8 ? `${fromUserId.slice(0, 4)}****${fromUserId.slice(-4)}` : fromUserId.slice(0, 2) + "****";
      abcSessionId = await abcSessionHelper.ensureSession(fromUserId, masked);
    } catch (err) {
      logger.warn("ensureAbcSession failed", { error: err instanceof Error ? err.message : String(err) });
    }
  }
  await flushPending(account.accountId, fromUserId, contextToken, sender);
  const userText = extractTextFromItems(msg.item_list);
  const imageItem = extractFirstImageUrl(msg.item_list);
  const fileItem = extractFirstFileItem(msg.item_list);
  if (session.state === "processing" && !userText.startsWith("/")) {
    return;
  }
  if (userText.startsWith("/")) {
    const updateSession = (partial) => {
      Object.assign(session, partial);
      sessionStore.save(account.accountId, session);
    };
    const ctx = {
      accountId: account.accountId,
      session,
      updateSession,
      clearSession: () => sessionStore.clear(account.accountId),
      getChatHistoryText: (limit) => sessionStore.getChatHistoryText(session, limit),
      text: userText
    };
    const result = routeCommand(ctx);
    if (result.handled && result.reply) {
      await sender.sendText(fromUserId, contextToken, result.reply);
      return;
    }
    if (result.handled && result.claudePrompt) {
      await sendToClaude(
        result.claudePrompt,
        imageItem,
        fileItem,
        fromUserId,
        contextToken,
        account,
        session,
        sessionStore,
        sender,
        config,
        activeControllers
      );
      return;
    }
    if (result.handled && result.sendFile) {
      await sender.sendFile(fromUserId, contextToken, result.sendFile);
      return;
    }
    if (result.handled) return;
  }
  if (!userText && !imageItem && !fileItem) {
    await sender.sendText(fromUserId, contextToken, "\u6682\u4E0D\u652F\u6301\u6B64\u7C7B\u578B\u6D88\u606F\uFF0C\u8BF7\u53D1\u9001\u6587\u5B57\u3001\u8BED\u97F3\u3001\u56FE\u7247\u6216\u6587\u4EF6");
    return;
  }
  if (abcSessionHelper && abcSessionId) {
    const userDisplay = userText || (imageItem ? "\uFF08\u56FE\u7247\uFF09" : fileItem ? "\uFF08\u6587\u4EF6\uFF09" : "\uFF08\u8BED\u97F3/\u5176\u4ED6\uFF09");
    await abcSessionHelper.appendMessage(abcSessionId, "user", userDisplay);
  }
  await sendToClaude(
    userText,
    imageItem,
    fileItem,
    fromUserId,
    contextToken,
    account,
    session,
    sessionStore,
    sender,
    config,
    activeControllers,
    abcSessionHelper,
    abcSessionId
  );
}
function extractTextFromItems(items) {
  return items.map((item) => extractText(item)).filter(Boolean).join("\n");
}
async function flushPending(accountId, toUserId, contextToken, sender) {
  const queue = loadPendingQueue(accountId);
  if (queue.length === 0) return;
  logger.info("Flushing pending queue", { accountId, pending: queue.length });
  const stillPending = [];
  for (const item of queue) {
    try {
      const chunks = splitMessage(item.text);
      for (const chunk of chunks) {
        await sender.sendText(toUserId, contextToken, chunk);
      }
    } catch (err) {
      logger.warn("Flush stopped at rate-limit, keeping remaining items queued", {
        accountId,
        flushed: queue.length - stillPending.length - 1,
        remaining: stillPending.length + 1,
        error: err instanceof Error ? err.message : String(err)
      });
      stillPending.push(item);
    }
  }
  savePendingQueue(accountId, stillPending);
  if (stillPending.length > 0 && stillPending.length === queue.length) {
    await sender.sendText(toUserId, contextToken, `\u23F3 \u8FD8\u6709 ${stillPending.length} \u6761\u6682\u5B58\u6D88\u606F\u672A\u80FD\u63A8\u9001\uFF0C\u518D\u53D1\u4EFB\u610F\u6D88\u606F\u6211\u4F1A\u7EE7\u7EED\u8865\u53D1\u3002`).catch(() => {
    });
  }
}
async function sendToClaude(userText, imageItem, fileItem, fromUserId, contextToken, account, session, sessionStore, sender, config, activeControllers, abcSessionHelper2 = null, abcSessionId = null) {
  session.state = "processing";
  sessionStore.save(account.accountId, session);
  const abortController = new AbortController();
  activeControllers.set(account.accountId, abortController);
  let flushTimer;
  sessionStore.addChatMessage(session, "user", userText || "(\u56FE\u7247)");
  const stopTyping = sender.startTyping(fromUserId, contextToken);
  try {
    let normalizeForSim = function(s) {
      return s.replace(/\s+/g, "").toLowerCase();
    }, bigramJaccard = function(a, b) {
      if (a === b) return 1;
      if (a.length < 2 || b.length < 2) return 0;
      const aCounts = /* @__PURE__ */ new Map();
      for (let i = 0; i < a.length - 1; i++) {
        const k = a.slice(i, i + 2);
        aCounts.set(k, (aCounts.get(k) || 0) + 1);
      }
      let inter = 0;
      for (let i = 0; i < b.length - 1; i++) {
        const k = b.slice(i, i + 2);
        const c = aCounts.get(k);
        if (c && c > 0) {
          inter++;
          if (c === 1) aCounts.delete(k);
          else aCounts.set(k, c - 1);
        }
      }
      const aSet = a.length - 1;
      const bSet = b.length - 1;
      const denom = aSet + bSet - inter;
      return denom > 0 ? inter / denom : 0;
    }, isSelfCorrectionDuplicate = function(text) {
      if (!lastEmitRecord) return false;
      const now = Date.now();
      if (now - lastEmitRecord.ts > DEDUP_WINDOW_MS) return false;
      const a = normalizeForSim(lastEmitRecord.text);
      const b = normalizeForSim(text);
      if (!a || !b) return false;
      if (a === b) return true;
      const minLen = Math.min(a.length, b.length);
      const maxLen = Math.max(a.length, b.length);
      if (minLen >= 16 && maxLen / minLen < 1.4) {
        const headLen = Math.floor(minLen * 0.7);
        if (headLen >= 16) {
          const aHead = a.slice(0, headLen);
          const bHead = b.slice(0, headLen);
          if (bigramJaccard(aHead, bHead) >= DEDUP_SIMILARITY) return true;
        }
      }
      return bigramJaccard(a, b) >= DEDUP_SIMILARITY;
    }, emitText = function(text, role) {
      if (!text.trim()) return;
      if (isSelfCorrectionDuplicate(text)) {
        logger.info("dropped self-correction duplicate", {
          role,
          length: text.length,
          preview: text.slice(0, 60)
        });
        return;
      }
      lastEmitRecord = { text, ts: Date.now() };
      if (pendingRetry) {
        const stuck = pendingRetry;
        pendingRetry = null;
        scheduleSend(stuck.text, stuck.role);
      }
      scheduleSend(text, role);
    }, scheduleSend = function(text, role) {
      if (!text.trim()) return;
      flushChain = flushChain.then(async () => {
        const chunks = splitMessage(text);
        for (let i = 0; i < chunks.length; i++) {
          try {
            await sender.sendText(fromUserId, contextToken, chunks[i]);
          } catch (err) {
            pendingRetry = { text: chunks.slice(i).join("\n\n"), role };
            logger.warn("emitText send failed, content retained for retry", {
              role,
              error: err instanceof Error ? err.message : String(err),
              retainedChunks: chunks.length - i
            });
            return;
          }
        }
        anySent = true;
        lastSentTime = Date.now();
      });
    };
    let images;
    if (imageItem) {
      const base64DataUri = await downloadImage(imageItem);
      if (base64DataUri) {
        const matches = base64DataUri.match(/^data:([^;]+);base64,(.+)$/);
        if (matches) {
          images = [
            {
              type: "image",
              source: {
                type: "base64",
                media_type: matches[1],
                data: matches[2]
              }
            }
          ];
        }
      }
    }
    let prompt = userText || "\u8BF7\u5206\u6790\u8FD9\u5F20\u56FE\u7247";
    if (fileItem) {
      const filePath = await downloadFile(fileItem);
      if (filePath) {
        const fileName = fileItem.file_item?.file_name || (0, import_node_path14.basename)(filePath);
        prompt = userText ? `${userText}

\u7528\u6237\u53D1\u9001\u4E86\u6587\u4EF6: ${fileName}
\u6587\u4EF6\u5DF2\u4FDD\u5B58\u5230: ${filePath}
\u8BF7\u5148\u8BFB\u53D6\u8FD9\u4E2A\u6587\u4EF6\u518D\u56DE\u7B54\u3002` : `\u7528\u6237\u53D1\u9001\u4E86\u6587\u4EF6: ${fileName}
\u6587\u4EF6\u5DF2\u4FDD\u5B58\u5230: ${filePath}
\u8BF7\u8BFB\u53D6\u8FD9\u4E2A\u6587\u4EF6\u5E76\u603B\u7ED3\u5176\u5185\u5BB9\u3002`;
      }
    }
    let anySent = false;
    let lastSentTime = Date.now();
    let pendingRetry = null;
    let flushChain = Promise.resolve();
    const DEDUP_WINDOW_MS = 1500;
    const DEDUP_SIMILARITY = 0.8;
    let lastEmitRecord = null;
    const router = new TurnRouter((msg) => emitText(filterToolNoise(msg.text), msg.role));
    const SILENCE_WARNING_MS = 5 * 60 * 1e3;
    const SILENCE_MESSAGES = [
      "\u6211\u8FD8\u5728\u5904\u7406\u4E2D\uFF0C\u8FD9\u4E2A\u95EE\u9898\u6709\u70B9\u590D\u6742\uFF0C\u8BF7\u518D\u7A0D\u7B49\u4E00\u4E0B",
      "\u6B63\u5728\u52AA\u529B\u5E72\u6D3B\u4E2D\uFF0C\u9A6C\u4E0A\u5C31\u6709\u7ED3\u679C\u4E86\uFF0C\u8BF7\u7A0D\u7B49\u7247\u523B",
      "\u6709\u70B9\u590D\u6742\u6B63\u5728\u5904\u7406\uFF0C\u518D\u7ED9\u6211\u4E00\u70B9\u65F6\u95F4\uFF0C\u5F88\u5FEB\u5C31\u597D",
      "\u5FEB\u597D\u4E86\u522B\u7740\u6025\uFF0C\u6B63\u5728\u6536\u5C3E\u9636\u6BB5\uFF0C\u9A6C\u4E0A\u7ED9\u4F60\u56DE\u590D",
      "\u8FD8\u5728\u8DD1\u5462\uFF0C\u4EFB\u52A1\u91CF\u6BD4\u8F83\u5927\uFF0C\u4E0D\u8FC7\u9A6C\u4E0A\u5C31\u80FD\u51FA\u7ED3\u679C\u4E86",
      "\u4EFB\u52A1\u6BD4\u60F3\u8C61\u7684\u590D\u6742\u4E00\u4E9B\uFF0C\u518D\u7B49\u7B49\u6211\uFF0C\u6B63\u5728\u5168\u529B\u5904\u7406",
      "\u6B63\u5728\u5904\u7406\u4E2D\uFF0C\u8FDB\u5C55\u987A\u5229\uFF0C\u518D\u7B49\u4E00\u4F1A\u513F\u5C31\u597D",
      "\u8FD8\u6CA1\u5B8C\u4E0D\u8FC7\u5DF2\u7ECF\u5FEB\u4E86\uFF0C\u518D\u7ED9\u6211\u4E00\u5206\u949F\u5C31\u80FD\u641E\u5B9A",
      "\u6211\u5728\u8BA4\u771F\u601D\u8003\u8FD9\u4E2A\u95EE\u9898\uFF0C\u8BF7\u518D\u7A0D\u7B49\u4E00\u4F1A\u513F",
      "\u7A0D\u5FAE\u6709\u70B9\u68D8\u624B\uFF0C\u4E0D\u8FC7\u5DF2\u7ECF\u5FEB\u89E3\u51B3\u4E86\uFF0C\u518D\u7B49\u6211\u4E00\u4E0B"
    ];
    flushTimer = setInterval(() => {
      if (Date.now() - lastSentTime > SILENCE_WARNING_MS) {
        const msg = SILENCE_MESSAGES[Math.floor(Math.random() * SILENCE_MESSAGES.length)];
        sender.sendText(fromUserId, contextToken, msg).catch(() => {
        });
        lastSentTime = Date.now();
      }
    }, 2e3);
    const queryOptions = {
      prompt,
      cwd: (session.workingDirectory || config.workingDirectory).replace(/^~/, (0, import_node_os5.homedir)()),
      // One stable Hermes thread per WeChat user, so each user gets their
      // own conversation history (and the abcyesno sidebar can group them).
      threadId: `wx-${fromUserId}`,
      resume: session.sdkSessionId,
      model: session.model,
      systemPrompt: [
        // Inject the real wall clock so the model can answer date/time
        // questions honestly. Without this it invents dates like "206 年
        // 8 月 5 日" or "8 月 25 号" because it has no real source.
        `\u5F53\u524D\u65F6\u95F4\uFF1A${formatNowForModel()}`,
        "\u4F60\u6B63\u5728\u901A\u8FC7\u5FAE\u4FE1\u4E0E\u7528\u6237\u5BF9\u8BDD\uFF0C\u4E0D\u662F\u5728\u7EC8\u7AEF\u91CC\u3002\u4E0D\u8981\u8BA9\u7528\u6237\u53BB\u7EC8\u7AEF\u64CD\u4F5C\u3002\u5982\u679C\u7528\u6237\u9700\u8981\u6587\u4EF6\uFF0C\u76F4\u63A5\u8F93\u51FA\u6587\u4EF6\u5730\u5740\u5C31\u884C\uFF0C\u4F1A\u81EA\u52A8\u8BC6\u522B\u89E3\u6790\u63A8\u9001\u6587\u4EF6\u5230\u7528\u6237\u7684\u5FAE\u4FE1\u4E2D\u3002",
        // Anti self-correction loop: the small model likes to reply, then
        // criticize its own reply, then reply again. Tell it to send the
        // final answer only — no meta-commentary on the previous draft.
        '\u56DE\u590D\u5FC5\u987B\u4E00\u6B21\u6027\u7ED9\u5230\u6700\u7EC8\u7B54\u6848\uFF0C\u4E0D\u8981"\u6211\u7406\u89E3\u9519\u4E86"\u5F0F\u7684\u81EA\u6211\u53CD\u601D\u91CD\u5199\uFF0C\u4E0D\u8981\u5728\u7B54\u6848\u524D\u540E\u8FFD\u52A0"\u521A\u624D\u90A3\u4E2A\u56DE\u7B54\u786E\u5B9E..."\u7684\u5143\u8BC4\u8BBA\u3002',
        '\u65E5\u671F\u3001\u65F6\u95F4\u3001\u661F\u671F\u3001\u7535\u8BDD\u53F7\u7801\u3001\u8EAB\u4EFD\u8BC1\u53F7\u3001\u7248\u672C\u53F7\u3001\u5F15\u7528\u7684\u6570\u5B57\u7B49\u4E00\u5207\u9700\u8981\u7CBE\u786E\u6027\u7684\u5185\u5BB9\uFF0C\u5FC5\u987B\u4E25\u683C\u57FA\u4E8E\u4E0A\u6587\u63D0\u4F9B\u7684"\u5F53\u524D\u65F6\u95F4"\u6216\u7528\u6237\u7ED9\u51FA\u7684\u771F\u5B9E\u6570\u636E\uFF1B\u4E0D\u77E5\u9053\u5C31\u8BF4\u4E0D\u77E5\u9053\uFF0C\u4E0D\u8981\u51ED\u5370\u8C61\u7F16\u9020\u3002',
        config.systemPrompt
      ].filter(Boolean).join("\n"),
      abortController,
      images,
      onText: (delta) => {
        router.onText(delta);
      },
      onTurnEnd: (stopReason) => {
        router.onTurnEnd(stopReason);
      }
    };
    let result = await claudeQuery(queryOptions);
    if (result.error && queryOptions.resume) {
      logger.warn("Resume failed, retrying without resume", { error: result.error, sessionId: queryOptions.resume });
      queryOptions.resume = void 0;
      session.sdkSessionId = void 0;
      sessionStore.save(account.accountId, session);
      const retryResult = await claudeQuery(queryOptions);
      Object.assign(result, retryResult);
    }
    clearInterval(flushTimer);
    router.drain();
    await flushChain;
    const MAX_TERMINAL_ATTEMPTS = 3;
    let terminalAttempt = 0;
    while (pendingRetry && terminalAttempt < MAX_TERMINAL_ATTEMPTS) {
      const stuck = pendingRetry;
      pendingRetry = null;
      terminalAttempt++;
      const delayMs = terminalAttempt * 5e3;
      logger.warn(`terminal retry ${terminalAttempt}/${MAX_TERMINAL_ATTEMPTS} for stranded content`, {
        role: stuck.role,
        delayMs,
        textLength: stuck.text.length
      });
      await new Promise((r) => setTimeout(r, delayMs));
      const chunks = splitMessage(stuck.text);
      let failed = false;
      for (let i = 0; i < chunks.length; i++) {
        try {
          await sender.sendText(fromUserId, contextToken, chunks[i]);
          anySent = true;
          lastSentTime = Date.now();
        } catch (err) {
          pendingRetry = { text: chunks.slice(i).join("\n\n"), role: stuck.role };
          logger.warn("terminal retry failed", {
            attempt: terminalAttempt,
            error: err instanceof Error ? err.message : String(err)
          });
          failed = true;
          break;
        }
      }
      if (!failed) break;
    }
    if (pendingRetry) {
      const queue = loadPendingQueue(account.accountId);
      queue.push({
        text: pendingRetry.text,
        role: pendingRetry.role,
        queuedAt: Date.now()
      });
      savePendingQueue(account.accountId, queue);
      logger.warn("content parked to pending queue", {
        role: pendingRetry.role,
        textLength: pendingRetry.text.length,
        queueSize: queue.length
      });
      await sender.sendText(fromUserId, contextToken, "\u23F3 \u90E8\u5206\u5185\u5BB9\u56E0\u5FAE\u4FE1\u5355\u6B21\u63A8\u9001\u4E0A\u9650\u6682\u5B58\uFF0C\u4E0B\u6B21\u4F60\u56DE\u590D\u4EFB\u610F\u6D88\u606F\u65F6\u81EA\u52A8\u8865\u53D1\u3002").catch(() => {
      });
      pendingRetry = null;
    }
    if (result.text) {
      if (result.error) {
        logger.warn("Claude query had error but returned text, using text", { error: result.error });
      }
      sessionStore.addChatMessage(session, "assistant", result.text);
      if (abcSessionHelper2 && abcSessionId) {
        await abcSessionHelper2.appendMessage(abcSessionId, "assistant", result.text);
      }
      if (!anySent) {
        const chunks = splitMessage(result.text);
        for (const chunk of chunks) {
          await sender.sendText(fromUserId, contextToken, chunk);
        }
      }
    } else if (result.error) {
      logger.error("Claude query error", { error: result.error });
      await sender.sendText(fromUserId, contextToken, "Claude \u5904\u7406\u8BF7\u6C42\u65F6\u51FA\u9519\uFF0C\u8BF7\u7A0D\u540E\u91CD\u8BD5\u3002");
    } else if (!anySent) {
      await sender.sendText(fromUserId, contextToken, "Claude \u65E0\u8FD4\u56DE\u5185\u5BB9\uFF08\u53EF\u80FD\u56E0\u6743\u9650\u88AB\u62D2\u800C\u7EC8\u6B62\uFF09");
    }
    session.sdkSessionId = result.sessionId || void 0;
    session.state = "idle";
    sessionStore.save(account.accountId, session);
    if (result.text) {
      const cwd = (session.workingDirectory || config.workingDirectory).replace(/^~/, (0, import_node_os5.homedir)());
      const detectedPaths = extractFilePathsFromText(result.text, cwd);
      const { existsSync: existsSync6 } = await import("node:fs");
      const { extname: extname2 } = await import("node:path");
      const pushable = detectedPaths.filter((f) => {
        const ext = extname2(f).toLowerCase();
        return AUTO_PUSH_EXTENSIONS.has(ext) && existsSync6(f);
      });
      if (pushable.length > 0) {
        const failedFiles = [];
        for (const filePath of pushable) {
          try {
            await sender.sendFile(fromUserId, contextToken, filePath);
          } catch {
            failedFiles.push(filePath);
          }
        }
        if (failedFiles.length > 0) {
          for (let attempt = 0; attempt < 3; attempt++) {
            const delay = (attempt + 1) * 15e3;
            logger.warn(`Rate-limited, retrying ${failedFiles.length} file(s) in ${delay / 1e3}s (attempt ${attempt + 1}/3)`);
            await new Promise((r) => setTimeout(r, delay));
            const stillFailed = [];
            for (const filePath of failedFiles) {
              try {
                await sender.sendFile(fromUserId, contextToken, filePath);
              } catch {
                stillFailed.push(filePath);
              }
            }
            if (stillFailed.length === 0) break;
            failedFiles.length = 0;
            failedFiles.push(...stillFailed);
          }
          if (failedFiles.length > 0) {
            logger.error("File delivery failed after all retries", { files: failedFiles });
            await sender.sendText(fromUserId, contextToken, `\u6587\u4EF6\u63A8\u9001\u5931\u8D25\uFF08\u670D\u52A1\u7AEF\u9650\u9891\uFF09\uFF0C\u8BF7\u7A0D\u540E\u91CD\u8BD5\u3002`).catch(() => {
            });
          }
        }
      }
    }
  } catch (err) {
    const isAbort = err instanceof Error && (err.name === "AbortError" || err.message.includes("abort"));
    if (isAbort) {
      logger.info("Claude query aborted by new message");
    } else {
      const errorMsg = err instanceof Error ? err.message : String(err);
      logger.error("Error in sendToClaude", { error: errorMsg });
      await sender.sendText(fromUserId, contextToken, "\u5904\u7406\u6D88\u606F\u65F6\u51FA\u9519\uFF0C\u8BF7\u7A0D\u540E\u91CD\u8BD5\u3002");
    }
    session.state = "idle";
    sessionStore.save(account.accountId, session);
  } finally {
    clearInterval(flushTimer);
    stopTyping();
    if (activeControllers.get(account.accountId) === abortController) {
      activeControllers.delete(account.accountId);
    }
  }
}
var import_node_readline, import_node_process, import_node_child_process, import_node_path14, import_node_fs12, import_node_os5, abcSessionHelper, inboundInterceptor, MAX_MESSAGE_LENGTH, AUTO_PUSH_EXTENSIONS, lastTurnInfo, PENDING_SENDS_FILE, PENDING_SENDS_MAX, PENDING_SENDS_TTL_MS, pendingProactiveSends;
var init_main = __esm({
  "electron/backend/wechat_bridge/src/main.ts"() {
    import_node_readline = require("node:readline");
    import_node_process = __toESM(require("node:process"));
    import_node_child_process = require("node:child_process");
    import_node_path14 = require("node:path");
    import_node_fs12 = require("node:fs");
    import_node_os5 = require("node:os");
    init_api();
    init_accounts();
    init_login();
    init_monitor();
    init_send();
    init_media();
    init_session();
    init_router();
    init_provider();
    init_turn_router();
    init_tool_noise_filter();
    init_config();
    init_logger();
    init_constants();
    init_types();
    init_pending_queue();
    abcSessionHelper = null;
    inboundInterceptor = null;
    MAX_MESSAGE_LENGTH = 4e3;
    AUTO_PUSH_EXTENSIONS = /* @__PURE__ */ new Set([
      ".png",
      ".jpg",
      ".jpeg",
      ".gif",
      ".webp",
      ".bmp",
      ".svg",
      ".ico",
      ".pdf",
      ".doc",
      ".docx",
      ".ppt",
      ".pptx",
      ".rtf",
      ".txt",
      ".md",
      ".csv",
      ".xlsx",
      ".xls",
      ".mp3",
      ".wav",
      ".m4a",
      ".mp4",
      ".mov"
    ]);
    lastTurnInfo = {
      contextToken: "",
      userId: ""
    };
    PENDING_SENDS_FILE = (0, import_node_path14.join)(DATA_DIR, "pending_sends.json");
    PENDING_SENDS_MAX = 10;
    PENDING_SENDS_TTL_MS = 24 * 60 * 60 * 1e3;
    pendingProactiveSends = loadPendingSends();
    if (import_node_process.default.env.WCC_CLI_MODE === "1") {
      const command = import_node_process.default.argv[2];
      if (command === "setup") {
        runSetup().catch((err) => {
          logger.error("Setup failed", { error: err instanceof Error ? err.message : String(err) });
          console.error("\u8BBE\u7F6E\u5931\u8D25:", err);
          import_node_process.default.exit(1);
        });
      } else {
        runDaemon().catch((err) => {
          logger.error("Daemon start failed", { error: err instanceof Error ? err.message : String(err) });
          console.error("\u542F\u52A8\u5931\u8D25:", err);
          import_node_process.default.exit(1);
        });
      }
    }
  }
});

// electron/backend/wechat_bridge/src/bridge.ts
var bridge_exports = {};
__export(bridge_exports, {
  appendAbcMessage: () => appendAbcMessage,
  beginQrBind: () => beginQrBind,
  claudeQuery: () => claudeQuery,
  clearSessionMap: () => clearSessionMap,
  ensureAbcSessionForWechatUser: () => ensureAbcSessionForWechatUser,
  getBridgeStatus: () => getBridgeStatus,
  getCurrentQrUrl: () => getCurrentQrUrl,
  getFromUserForSession: () => getFromUserForSession,
  getQrDataUrl: () => getQrDataUrl,
  getSessionIdForFromUser: () => getSessionIdForFromUser,
  makeWechatThreadId: () => makeWechatThreadId,
  maskAccountId: () => maskAccountId,
  onBridgeStatus: () => onBridgeStatus,
  restartBridge: () => restartBridge,
  sendTestMessage: () => sendTestMessage,
  setAbcStorage: () => setAbcStorage,
  setInboundInterceptor: () => setInboundInterceptor,
  setSessionIdForFromUser: () => setSessionIdForFromUser,
  startBridge: () => startBridge,
  stopBridge: () => stopBridge,
  tailLogs: () => tailLogs,
  unbindAccount: () => unbindAccount
});
module.exports = __toCommonJS(bridge_exports);
var import_node_path15 = require("node:path");
var import_node_fs13 = require("node:fs");
init_accounts();
init_login();
init_main();
init_constants();
init_logger();
init_provider();
init_main();
var SESSION_MAP_FILE = (0, import_node_path15.join)(DATA_DIR, "wx_session_map.json");
var sessionMapCache = /* @__PURE__ */ new Map();
function loadSessionMap() {
  try {
    if ((0, import_node_fs13.existsSync)(SESSION_MAP_FILE)) {
      return JSON.parse((0, import_node_fs13.readFileSync)(SESSION_MAP_FILE, "utf-8"));
    }
  } catch (err) {
    logger.warn("loadSessionMap failed", { error: err instanceof Error ? err.message : String(err) });
  }
  return {};
}
function saveSessionMap(map) {
  try {
    (0, import_node_fs13.mkdirSync)(DATA_DIR, { recursive: true });
    (0, import_node_fs13.writeFileSync)(SESSION_MAP_FILE, JSON.stringify(map, null, 2), "utf-8");
  } catch (err) {
    logger.warn("saveSessionMap failed", { error: err instanceof Error ? err.message : String(err) });
  }
}
function getSessionIdForFromUser(fromUserId) {
  if (!fromUserId) return null;
  if (sessionMapCache.has(fromUserId)) return sessionMapCache.get(fromUserId) || null;
  const map = loadSessionMap();
  const sid = map[fromUserId];
  if (sid) sessionMapCache.set(fromUserId, sid);
  return sid || null;
}
function setSessionIdForFromUser(fromUserId, sessionId) {
  if (!fromUserId || !sessionId) return;
  sessionMapCache.set(fromUserId, sessionId);
  const map = loadSessionMap();
  map[fromUserId] = sessionId;
  saveSessionMap(map);
}
function clearSessionMap() {
  sessionMapCache.clear();
  try {
    (0, import_node_fs13.rmSync)(SESSION_MAP_FILE, { force: true });
  } catch {
  }
}
function getFromUserForSession(sessionId) {
  if (!sessionId) return null;
  for (const [user, sid] of sessionMapCache.entries()) {
    if (sid === sessionId) return user;
  }
  const map = loadSessionMap();
  for (const [user, sid] of Object.entries(map)) {
    if (sid === sessionId) {
      sessionMapCache.set(user, sid);
      return user;
    }
  }
  return null;
}
function makeWechatThreadId(fromUserId) {
  return `wx-${fromUserId}`;
}
var abcStorage = null;
var abcNotifySessionsUpdated = null;
function setAbcStorage(storage, notifySessionsUpdated2) {
  abcStorage = storage;
  abcNotifySessionsUpdated = notifySessionsUpdated2 || null;
  try {
    if (typeof setAbcSessionHelper === "function") {
      setAbcSessionHelper({
        ensureSession: ensureAbcSessionForWechatUser,
        appendMessage: appendAbcMessage
      });
    }
  } catch (err) {
    logger.warn("setAbcStorage: failed to wire main.ts helper", {
      error: err instanceof Error ? err.message : String(err)
    });
  }
}
function notifySessionsUpdated() {
  try {
    abcNotifySessionsUpdated?.();
  } catch {
  }
}
async function ensureAbcSessionForWechatUser(fromUserId, fromUserMasked) {
  if (!abcStorage) return null;
  if (!fromUserId) return null;
  const existing = getSessionIdForFromUser(fromUserId);
  if (existing) return existing;
  const label = fromUserMasked && String(fromUserMasked).trim() || String(fromUserId).slice(0, 8);
  const session = await abcStorage.createSession("default", `\u5FAE\u4FE1 \xB7 ${label}`, { source: "wechat" });
  setSessionIdForFromUser(fromUserId, session.id);
  notifySessionsUpdated();
  return session.id;
}
async function appendAbcMessage(sessionId, role, content) {
  if (!abcStorage || !sessionId) return;
  if (!content || !String(content).trim()) return;
  try {
    await abcStorage.appendSessionMessage(sessionId, role, String(content));
    notifySessionsUpdated();
  } catch (err) {
    logger.warn("appendAbcMessage failed", {
      sessionId,
      role,
      error: err instanceof Error ? err.message : String(err)
    });
  }
}
var runtime = null;
var statusListeners = [];
var currentState = "idle";
var currentDetail;
var bindingActive = false;
var currentQrUrl = null;
function pushState(state, detail) {
  currentState = state;
  currentDetail = detail;
  const status = {
    state,
    bound: !!loadLatestAccount(),
    detail,
    ts: Date.now()
  };
  try {
    const acc = loadLatestAccount();
    if (acc) status.accountId = acc.accountId;
  } catch {
  }
  logger.info("bridge state", { state, detail: detail || "" });
  for (const cb of [...statusListeners]) {
    try {
      cb(status);
    } catch {
    }
  }
}
function onBridgeStatus(cb) {
  statusListeners.push(cb);
  return () => {
    statusListeners = statusListeners.filter((l) => l !== cb);
  };
}
function getBridgeStatus() {
  const status = {
    state: currentState,
    bound: false,
    detail: currentDetail,
    ts: Date.now()
  };
  try {
    const acc = loadLatestAccount();
    if (acc) {
      status.bound = true;
      status.accountId = acc.accountId;
    }
  } catch {
  }
  return status;
}
function maskAccountId(id) {
  if (!id || id.length <= 8) return id ? `${id.slice(0, 2)}****` : "";
  return `${id.slice(0, 4)}****${id.slice(-4)}`;
}
async function startBridge() {
  if (runtime) return getBridgeStatus();
  const account = loadLatestAccount();
  if (!account) {
    pushState("idle", "\u672A\u7ED1\u5B9A\u5FAE\u4FE1\u8D26\u53F7");
    return getBridgeStatus();
  }
  await connectWithAccount();
  return getBridgeStatus();
}
async function connectWithAccount() {
  pushState("connecting");
  try {
    runtime = await createDaemonRuntime();
    if (!runtime) {
      pushState("error", "\u8D26\u53F7\u8BFB\u53D6\u5931\u8D25\uFF0C\u8BF7\u91CD\u65B0\u7ED1\u5B9A");
      return;
    }
    pushState("connected", maskAccountId(runtime.accountId));
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.error("connectWithAccount failed", { error: msg });
    pushState("error", msg);
  }
}
function stopBridge() {
  bindingActive = false;
  if (runtime) {
    runtime.stop();
    runtime = null;
  }
  pushState("idle");
}
async function restartBridge() {
  stopBridge();
  return startBridge();
}
async function beginQrBind() {
  bindingActive = true;
  const { qrcodeUrl, qrcodeId } = await startQrLogin();
  currentQrUrl = qrcodeUrl;
  pushState("awaiting_qr");
  void pollBindLoop(qrcodeId);
  return { qrcodeUrl };
}
async function pollBindLoop(qrcodeId) {
  let id = qrcodeId;
  while (bindingActive) {
    try {
      const account = await waitForQrScan(id);
      bindingActive = false;
      currentQrUrl = null;
      logger.info("QR bind success", { accountId: account.accountId });
      await connectWithAccount();
      return;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (!bindingActive) return;
      if (msg.includes("expired")) {
        pushState("qr_expired");
        try {
          const { qrcodeUrl, qrcodeId: newId } = await startQrLogin();
          currentQrUrl = qrcodeUrl;
          id = newId;
          pushState("awaiting_qr", "\u4E8C\u7EF4\u7801\u5DF2\u5237\u65B0");
          continue;
        } catch (e) {
          pushState("error", `\u5237\u65B0\u4E8C\u7EF4\u7801\u5931\u8D25: ${e instanceof Error ? e.message : String(e)}`);
          return;
        }
      }
      pushState("error", msg);
      return;
    }
  }
}
function getCurrentQrUrl() {
  return currentQrUrl;
}
async function getQrDataUrl() {
  if (!currentQrUrl) return null;
  try {
    const QRCode = (await Promise.resolve().then(() => __toESM(require_lib()))).default;
    return await QRCode.toDataURL(currentQrUrl, { type: "png", width: 320, margin: 2 });
  } catch (err) {
    logger.error("getQrDataUrl failed", { error: err instanceof Error ? err.message : String(err) });
    return null;
  }
}
function unbindAccount() {
  stopBridge();
  try {
    const accountsDir = (0, import_node_path15.join)(DATA_DIR, "accounts");
    if ((0, import_node_fs13.existsSync)(accountsDir)) (0, import_node_fs13.rmSync)(accountsDir, { recursive: true, force: true });
    const bufPath = (0, import_node_path15.join)(DATA_DIR, "get_updates_buf");
    if ((0, import_node_fs13.existsSync)(bufPath)) (0, import_node_fs13.rmSync)(bufPath, { force: true });
    clearSessionMap();
    logger.info("Account unbound");
  } catch (err) {
    logger.error("unbindAccount failed", { error: err instanceof Error ? err.message : String(err) });
    throw err;
  }
  pushState("idle", "\u5DF2\u89E3\u7ED1");
}
function tailLogs(maxLines = 120) {
  const logDir = (0, import_node_path15.join)(DATA_DIR, "logs");
  let files = [];
  try {
    files = (0, import_node_fs13.readdirSync)(logDir).filter((f) => f.startsWith("bridge-") && f.endsWith(".log")).sort();
  } catch {
    return [];
  }
  if (files.length === 0) return [];
  const latest = files[files.length - 1];
  try {
    const content = (0, import_node_fs13.readFileSync)((0, import_node_path15.join)(logDir, latest), "utf-8");
    const lines = content.split("\n").filter(Boolean);
    return lines.slice(-maxLines);
  } catch {
    return [];
  }
}
async function sendTestMessage(text) {
  const { getLastTurnInfo: getLastTurnInfo2, queueProactiveSend: queueProactiveSend2 } = await Promise.resolve().then(() => (init_main(), main_exports));
  if (!runtime) {
    queueProactiveSend2(text);
    return { ok: false, queued: true, error: "\u5FAE\u4FE1\u6865\u672A\u8FDE\u63A5\uFF0C\u901A\u77E5\u5DF2\u6392\u961F\uFF1A\u6865\u6062\u590D\u4E14\u7528\u6237\u4E0B\u6B21\u53D1\u6D88\u606F\u540E\u81EA\u52A8\u8865\u53D1" };
  }
  try {
    const turn = getLastTurnInfo2();
    if (!turn.contextToken || !turn.userId) {
      queueProactiveSend2(text);
      return { ok: false, queued: true, error: "\u5FAE\u4FE1\u56DE\u590D\u7A97\u53E3\u672A\u6FC0\u6D3B\uFF0C\u901A\u77E5\u5DF2\u6392\u961F\uFF1A\u7528\u6237\u4E0B\u6B21\u4ECE\u5FAE\u4FE1\u7ED9\u673A\u5668\u4EBA\u53D1\u4EFB\u610F\u4E00\u6761\u6D88\u606F\u540E\u81EA\u52A8\u8865\u53D1" };
    }
    await runtime.sender.sendText(turn.userId, turn.contextToken, text);
    return { ok: true };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.warn("sendTestMessage failed", { error: msg });
    if (/stale session|context_token/i.test(msg)) {
      queueProactiveSend2(text);
      return { ok: false, queued: true, error: "\u5FAE\u4FE1\u56DE\u590D\u7A97\u53E3\u5DF2\u8FC7\u671F\uFF0C\u901A\u77E5\u5DF2\u6392\u961F\uFF1A\u7528\u6237\u4E0B\u6B21\u4ECE\u5FAE\u4FE1\u53D1\u4EFB\u610F\u4E00\u6761\u6D88\u606F\u540E\u81EA\u52A8\u8865\u53D1" };
    }
    return { ok: false, error: msg };
  }
}
try {
  (0, import_node_fs13.mkdirSync)(DATA_DIR, { recursive: true });
} catch {
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  appendAbcMessage,
  beginQrBind,
  claudeQuery,
  clearSessionMap,
  ensureAbcSessionForWechatUser,
  getBridgeStatus,
  getCurrentQrUrl,
  getFromUserForSession,
  getQrDataUrl,
  getSessionIdForFromUser,
  makeWechatThreadId,
  maskAccountId,
  onBridgeStatus,
  restartBridge,
  sendTestMessage,
  setAbcStorage,
  setInboundInterceptor,
  setSessionIdForFromUser,
  startBridge,
  stopBridge,
  tailLogs,
  unbindAccount
});
