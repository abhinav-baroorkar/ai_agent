// One conversation with one bot over /ws?bot=<id>. Reconnects by itself.
// Events: 'online' / 'offline', 'refused' (detail: {code, message}; the server
// will not take this connection, so there is no retry), 'audio' (detail: ArrayBuffer), and one event per
// server message type ('state', 'transcript', 'tool', 'reply_delta', 'reply',
// 'tts_start', 'tts_end', 'error') with the parsed message as detail.
import { openSocket } from './api.js';

const RETRY_MIN = 800;
const RETRY_MAX = 10000;
// Close codes that mean "don't come back": the bot is gone, or too many connections.
const FINAL = {
  1008: 'This bot is no longer available.',
  1013: 'Too many chats are open at once. Close one, then open this one again.',
};

export class Conversation extends EventTarget {
  constructor(botId, { speak = false } = {}) {
    super();
    this.botId = botId;
    this.speak = speak;
    this.online = false;
    this.state = 'idle';
    this.closed = false;
    this.waiting = null;
    this.retry = RETRY_MIN;
    this.ws = null;
    this.connect();
  }

  connect() {
    if (this.closed) return;
    let ws;
    try {
      ws = openSocket(this.botId, this.speak);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.online = true;
      this.retry = RETRY_MIN;
      this.emit('online');
    };
    ws.onmessage = (ev) => this.onMessage(ev.data);
    ws.onerror = () => {};
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      const was = this.online;
      this.online = false;
      this.state = 'idle';
      this.waiting = null;
      if (was) this.emit('state', { type: 'state', state: 'idle' });
      this.emit('offline');
      const code = ev && ev.code;
      if (FINAL[code]) {
        this.closed = true;
        this.emit('refused', { code, message: FINAL[code] });
        return;
      }
      this.scheduleRetry();
    };
  }

  scheduleRetry() {
    if (this.closed) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), this.retry);
    this.retry = Math.min(this.retry * 1.7, RETRY_MAX);
  }

  onMessage(data) {
    if (typeof data !== 'string') {
      this.emit('audio', data);
      return;
    }
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (!msg || typeof msg.type !== 'string') return;
    if (msg.type === 'state') this.state = msg.state;
    this.emit(msg.type, msg);
    if (msg.type === 'state' && msg.state === 'idle' && this.waiting) {
      const next = this.waiting;
      this.waiting = null;
      next();
    }
  }

  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  on(type, fn) {
    this.addEventListener(type, (ev) => fn(ev.detail));
    return this;
  }

  post(payload) {
    if (!this.online) return false;
    this.ws.send(payload instanceof ArrayBuffer ? payload : JSON.stringify(payload));
    return true;
  }

  // Runs `next` once the server is idle. If a turn is running it is cancelled
  // first: the server refuses a new message while busy, it does not queue it.
  whenIdle(next) {
    if (this.state === 'idle') return next();
    this.waiting = next;
    this.cancel();
  }

  sendText(text) { return this.post({ type: 'text', text }); }
  startAudio() { return this.post({ type: 'audio_start', sample_rate: 16000 }); }
  sendAudio(buf) { return this.post(buf); }
  stopAudio() { return this.post({ type: 'audio_stop' }); }
  cancel() { return this.post({ type: 'cancel' }); }
  reset() { return this.post({ type: 'reset' }); }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    if (ws) ws.close();
  }
}
