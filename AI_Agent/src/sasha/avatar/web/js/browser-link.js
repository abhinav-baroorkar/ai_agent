// The live view's own connection, /ws/browser: one viewer at a time, frames
// only while we say we are watching, one frame in flight.
// Events: 'status' (detail: 'connecting' | 'open' | 'retrying' | 'busy' | 'refused'
// | 'offline' | 'closed'), 'state', 'frame', 'stopped' and 'error' with the message.
import { openBrowserSocket } from './api.js';

const BUSY_TRIES = 4; // another viewer has the picture: try again after a second, a few times
const BUSY_WAIT = 1000;
const RETRY_MIN = 1000;
const RETRY_MAX = 8000;
const STABLE_MS = 5000; // a connection must last this long before the retry counters reset

export class BrowserLink extends EventTarget {
  constructor() {
    super();
    this.ws = null;
    this.wanted = false; // should there be a connection at all
    this.watching = false; // should frames be flowing
    this.status = 'closed';
    this.busyTries = 0;
    this.retry = RETRY_MIN;
    this.timer = null;
    this.stable = null;
  }

  on(type, fn) {
    this.addEventListener(type, (ev) => fn(ev.detail));
    return this;
  }

  set(status) {
    if (status === this.status) return;
    this.status = status;
    this.dispatchEvent(new CustomEvent('status', { detail: status }));
  }

  open() {
    if (this.wanted) return;
    this.wanted = true;
    this.busyTries = 0;
    this.connect();
  }

  // Called by the "Try again" button after 'busy' or 'refused'.
  again() {
    this.wanted = true;
    this.busyTries = 0;
    this.connect();
  }

  close() {
    this.wanted = false;
    clearTimeout(this.timer);
    clearTimeout(this.stable);
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      this.post(ws, { type: 'browser_watch', on: false });
      ws.close();
    }
    this.set('closed');
  }

  connect() {
    clearTimeout(this.timer);
    if (!this.wanted || this.ws) return;
    this.set(this.busyTries ? 'retrying' : 'connecting');
    let ws;
    try {
      ws = openBrowserSocket();
    } catch {
      return this.later();
    }
    this.ws = ws;
    ws.onopen = () => {
      // Not reset here: a server that accepts and closes at once would otherwise be
      // retried every second for ever.
      this.stable = setTimeout(() => { this.busyTries = 0; this.retry = RETRY_MIN; }, STABLE_MS);
      this.set('open');
      this.post(ws, { type: 'browser_watch', on: this.watching });
    };
    ws.onmessage = (ev) => this.onMessage(ev.data);
    ws.onerror = () => {};
    ws.onclose = (ev) => {
      clearTimeout(this.stable);
      if (this.ws !== ws) return;
      this.ws = null;
      if (!this.wanted) return;
      const code = ev && ev.code;
      if (code === 1013) { // someone else is watching
        this.busyTries += 1;
        if (this.busyTries > BUSY_TRIES) return this.set('busy');
        this.set('retrying');
        this.timer = setTimeout(() => this.connect(), BUSY_WAIT);
      } else if (code === 1008) {
        this.set('refused'); // the server will not take this window; no point retrying
      } else {
        this.later();
      }
    };
  }

  later() {
    this.set('offline');
    this.timer = setTimeout(() => this.connect(), this.retry);
    this.retry = Math.min(this.retry * 2, RETRY_MAX);
  }

  onMessage(data) {
    if (typeof data !== 'string') return;
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (!msg || typeof msg.type !== 'string') return;
    const kinds = { browser_state: 'state', browser_frame: 'frame', browser_stopped: 'stopped', error: 'error' };
    const kind = kinds[msg.type];
    if (kind) this.dispatchEvent(new CustomEvent(kind, { detail: msg }));
  }

  post(ws, payload) {
    if (!ws || ws.readyState !== undefined && ws.readyState !== 1) return false;
    ws.send(JSON.stringify(payload));
    return true;
  }

  // Frames are produced only while someone is really looking.
  watch(on) {
    if (this.watching === on) return;
    this.watching = on;
    if (this.status === 'open') this.post(this.ws, { type: 'browser_watch', on });
  }

  ack(seq) {
    if (this.status === 'open') this.post(this.ws, { type: 'browser_ack', seq });
  }

  // Returns false when the socket can't carry it, so the caller can use the HTTP route.
  stop() {
    return this.status === 'open' && this.post(this.ws, { type: 'browser_stop' });
  }
}
