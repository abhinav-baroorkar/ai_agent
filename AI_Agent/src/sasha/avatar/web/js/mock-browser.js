// A stand-in for the assistant's browser, for ?mock=1. It paints its own
// "web pages" on a canvas, so no image files are needed. Switches:
//   ?mock=1&browser=running   already running when the panel opens
//   ?mock=1&browser=off       the browser connector is switched off in Settings
//   ?mock=1&browser=busy      another viewer already has the picture (close code 1013)
//   ?mock=1&browser=down      the connection keeps failing (the offline state)
// Otherwise it starts when a message goes to a bot that has the browser connector.
const mode = new URLSearchParams(location.search).get('browser') || '';
const viewers = new Set();
const PAGES = [
  // The title looks like markup on purpose: the panel must show it as plain text.
  { site: 'recipes.example', title: '<img src=x onerror=alert(1)> Ten weeknight dinners', ink: '#7a3b12', bar: 'accounts.example-bank.test/login' },
  { site: 'weather.example', title: 'Forecast for the week', ink: '#0f4c5c', bar: 'weather.example/week' },
  // A site spelled with a look-alike letter (Cyrillic) and a title with right-to-left text:
  // the panel must show "(unusual address)" and keep the title from reordering its line.
  { site: 'w\u0435ather.example', title: '\u202Emoc.elpmaxe-knab \u05E9\u05DC\u05D5\u05DD Sign in', ink: '#5c0f3a', bar: 'weather.example/alerts' },
];
const state = {
  enabled: mode !== 'off', running: false, agent: 'idle', holder: null, holder_bot: null, tabs: [],
  guard: { allowed: 0, refused: {}, background_refused: 0 },
};
let current = 0;
let seq = 0;
let frame = 0;
let stopTimer = null;
const canvas = document.createElement('canvas');
canvas.width = 960;
canvas.height = 600;

const snapshot = () => JSON.parse(JSON.stringify(state));
const tell = (msg) => viewers.forEach((v) => v.emit(msg));
const changed = () => tell({ type: 'browser_state', ...snapshot() });

function tabs() {
  return PAGES.map((p, i) => ({ tab: i + 1, site: p.site, title: p.title, current: i === current }));
}

// One fake page. It draws its own address bar inside the picture, with a different
// address from the real one, to show why the panel's own Site line is the one to trust.
function paint() {
  const page = PAGES[current];
  const c = canvas.getContext('2d');
  frame += 1;
  c.fillStyle = '#fbfaf7';
  c.fillRect(0, 0, 960, 600);
  c.fillStyle = '#e4e2dc';
  c.fillRect(0, 0, 960, 46);
  c.fillStyle = '#fff';
  c.fillRect(110, 9, 620, 28);
  c.fillStyle = '#333';
  c.font = '15px sans-serif';
  c.fillText(`🔒 ${page.bar}`, 122, 28);
  c.fillStyle = page.ink;
  c.font = 'bold 34px serif';
  c.fillText(page.title.replace(/<[^>]*>\s*|[^\x20-\x7E]/g, ''), 60, 118);
  const scroll = (frame * 3) % 120;
  c.fillStyle = '#c9c5bb';
  for (let i = 0; i < 9; i++) c.fillRect(60, 160 + i * 44 - scroll * 0.3, 560 + ((i * 97) % 220), 14);
  c.fillStyle = page.ink;
  c.globalAlpha = 0.18;
  c.fillRect(60, 152 + (Math.floor(frame / 6) % 9) * 44 - scroll * 0.3, 800, 30); // the line being "read"
  c.globalAlpha = 1;
  return canvas.toDataURL('image/jpeg', 0.6).split(',')[1];
}

function sendFrame(viewer) {
  if (!state.running || !viewer.watching || viewer.waiting) return;
  viewer.waiting = true;
  seq += 1;
  viewer.emit({ type: 'browser_frame', tab: current + 1, seq, w: 960, h: 600, jpeg: paint() });
}

// The browser is used for a turn: it starts if needed, works for a while, then idles out.
export function use() {
  if (!state.enabled || mode === 'busy') return;
  clearTimeout(stopTimer);
  const holder_bot = { id: 'b-web', name: 'Web researcher' };
  Object.assign(state, { running: true, agent: 'working', holder: 'conversation-1', holder_bot, tabs: tabs() });
  state.guard.allowed += 7;
  state.guard.refused = { private_network: 2, not_https: 1 };
  state.guard.background_refused += 4;
  changed();
  const next = () => { current = (current + 1) % PAGES.length; state.tabs = tabs(); state.guard.allowed += 5; changed(); };
  setTimeout(next, 6000);
  setTimeout(next, 9000);
  setTimeout(() => { state.agent = 'idle'; state.holder = null; state.holder_bot = null; changed(); }, 11000);
  stopTimer = setTimeout(() => stop('idle'), 40000);
}

export function stop(reason) {
  clearTimeout(stopTimer);
  const was = state.running;
  Object.assign(state, { running: false, agent: 'idle', holder: null, holder_bot: null, tabs: [], guard: { allowed: 0, refused: {}, background_refused: 0 } });
  if (was) tell({ type: 'browser_stopped', reason });
  changed();
}

setInterval(() => viewers.forEach(sendFrame), 220);

export function browserFetch(method, action) {
  if (method === 'GET' && !action) return snapshot();
  if (method === 'POST' && action === 'stop') {
    stop('owner');
    return { ok: true };
  }
  return undefined;
}

export class MockBrowserSocket {
  constructor() {
    this.watching = false;
    this.waiting = false;
    if (mode === 'down') {
      setTimeout(() => this.onclose && this.onclose({ code: 1006 }), 120);
      return;
    }
    if (mode === 'busy' || viewers.size >= 1) { // one viewer at a time, like the real server
      setTimeout(() => this.onclose && this.onclose({ code: 1013 }), 120);
      return;
    }
    viewers.add(this);
    setTimeout(() => {
      if (this.onopen) this.onopen();
      this.emit({ type: 'browser_state', ...snapshot() });
      if (mode === 'running') use();
    }, 150);
  }

  emit(msg) {
    if (viewers.has(this) && this.onmessage) this.onmessage({ data: JSON.stringify(msg) });
  }

  send(raw) {
    const msg = JSON.parse(raw);
    if (msg.type === 'browser_watch') {
      this.watching = !!msg.on;
      this.waiting = false;
    }
    if (msg.type === 'browser_ack' && msg.seq === seq) this.waiting = false;
    if (msg.type === 'browser_stop') stop('owner');
  }

  close() {
    if (!viewers.delete(this)) return;
    setTimeout(() => this.onclose && this.onclose({ code: 1000 }), 0);
  }
}
