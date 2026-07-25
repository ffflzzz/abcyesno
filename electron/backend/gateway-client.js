const WebSocket = require('ws');
const { EventEmitter } = require('events');
const { log } = require('./logger');

class GatewayClient extends EventEmitter {
  constructor({ url, token }) {
    super();
    this.url = url;
    this.token = token;
    this.ws = null;
    this.ready = false;
    this.reqId = 0;
    this.pending = new Map(); // id -> { resolve, reject, timeout }
    this.reconnectDelay = 1000;
    this.maxReconnectDelay = 30000;
    this.intentionalClose = false;
    this.reconnectTimer = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.intentionalClose = false;
      const url = this.token ? `${this.url}?token=${encodeURIComponent(this.token)}` : this.url;
      log('gateway-client', `connecting to ${url}`);
      this.ws = new WebSocket(url);

      let settled = false;

      const cleanup = () => {
        try {
          if (this.ws) {
            this.ws.off('open', onOpen);
            this.ws.off('error', onError);
            this.ws.off('close', onClose);
          }
        } catch (_) {}
      };

      const onOpen = () => {
        if (settled) return;
        settled = true;
        cleanup();
        log('gateway-client', 'websocket open');
        this.ready = true;
        this.reconnectDelay = 1000;
        this.emit('open');
        resolve();
      };

      const onError = (err) => {
        const msg = err && err.message ? err.message : String(err);
        log('gateway-client', `websocket error: ${msg}`);
        this.emit('error', err);
        if (!settled) {
          settled = true;
          cleanup();
          this.ws = null;
          reject(new Error(msg));
        }
      };

      const onClose = (code, reason) => {
        log('gateway-client', `websocket close code=${code} reason=${reason ? reason.toString() : ''}`);
        this.ready = false;
        this.ws = null;
        this.emit('close', { code, reason: reason ? reason.toString() : '' });
        if (!this.intentionalClose) this._scheduleReconnect();
      };

      this.ws.once('open', onOpen);
      this.ws.once('error', onError);
      this.ws.on('message', (data) => this._onMessage(data));
      this.ws.once('close', onClose);

      // Safety net: if neither open nor error fires within 10s, reject and
      // schedule a reconnect so the bridge can recover from a slow Hermes start.
      setTimeout(() => {
        if (!settled) {
          settled = true;
          cleanup();
          const err = new Error('gateway websocket connection timed out');
          log('gateway-client', err.message);
          try { this.ws.terminate(); } catch (_) {}
          this.ws = null;
          if (!this.intentionalClose) this._scheduleReconnect();
          reject(err);
        }
      }, 10000);
    });
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) return;
    log('gateway-client', `reconnect in ${this.reconnectDelay}ms`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect().catch(() => {});
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
    }, this.reconnectDelay);
  }

  _onMessage(data) {
    let text;
    try {
      text = data.toString('utf-8');
    } catch (err) {
      log('gateway-client', `decode error: ${err.message}`);
      return;
    }
    const lines = text.split(/\r?\n/);
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const msg = JSON.parse(line);
        this._handleMessage(msg);
      } catch (err) {
        log('gateway-client', `parse error: ${err.message}`);
      }
    }
  }

  _handleMessage(msg) {
    if (msg.method === 'event' && msg.params) {
      this.emit('event', msg.params.type, msg.params);
      this.emit(msg.params.type, msg.params);
      return;
    }
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const { resolve, reject, timeout } = this.pending.get(msg.id);
      clearTimeout(timeout);
      this.pending.delete(msg.id);
      if (msg.error) {
        reject(new Error(msg.error.message || 'gateway error'));
      } else {
        resolve(msg.result);
      }
    }
  }

  request(method, params = {}, timeoutMs = 60000) {
    return new Promise((resolve, reject) => {
      if (!this.ready || !this.ws) {
        reject(new Error('gateway not connected'));
        return;
      }
      const id = ++this.reqId;
      const payload = { jsonrpc: '2.0', id, method, params };
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`gateway request timeout: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timeout });
      try {
        this.ws.send(JSON.stringify(payload));
      } catch (err) {
        clearTimeout(timeout);
        this.pending.delete(id);
        reject(err);
      }
    });
  }

  notify(method, params = {}) {
    if (!this.ready || !this.ws) return;
    this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params }));
  }

  close() {
    this.intentionalClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    for (const { reject, timeout } of this.pending.values()) {
      clearTimeout(timeout);
      reject(new Error('gateway client closed'));
    }
    this.pending.clear();
    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }
    this.ready = false;
  }
}

module.exports = { GatewayClient };
