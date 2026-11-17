// A stand-in for the assistant's browser, for ?mock=1. It paints its own
// "web pages" on a canvas, so no image files are needed. Switches:
//   ?mock=1&browser=running   already running when the panel opens
//   ?mock=1&browser=off       the browser connector is switched off in Settings
//   ?mock=1&browser=noscreen  taking over is refused: no screen (409)
//   ?mock=1&browser=old       a server before take-over (no mode in the state)
//   ?mock=1&browser=busy      another viewer already has the picture (close code 1013)
//   ?mock=1&browser=down      the connection keeps failing (the offline state)
//   ?mock=1&browser=oldwindow the brain restarted: this window's token is refused (socket 1006, GET 401)
//   ?mock=1&browser=forbidden the brain refuses this window outright (socket 1006, GET 403)
//   ?mock=1&browser=restoring a hand-back cleans up for 5 s first (mode `restoring`)
//   ?mock=1&browser=stuck     ...and the clean-up keeps failing (with `restore_problem`)
// Otherwise it starts when a message goes to a bot that has the browser connector.
// several switches can be given with commas, e.g. ?browser=running,noscreen
const flags = (new URLSearchParams(location.search).get('browser') || '').split(',');
const has = (f) => flags.includes(f);
const mode = flags.find((f) => ['off', 'busy', 'down', 'running'].includes(f)) || '';
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
  mode: 'assistant', owner_until: null, owner_refused: {}, restore_problem: null, person_check: false,
  guard: { allowed: 0, refused: {}, background_refused: 0 },
};
let current = 0;
let seq = 0;
let frame = 0;
let stopTimer = null;
const canvas = document.createElement('canvas');
canvas.width = 960;
canvas.height = 600;

const snapshot = () => {
  const out = JSON.parse(JSON.stringify(state));
  if (has('old')) { delete out.mode; delete out.owner_until; delete out.owner_refused; if (out.holder_bot) delete out.holder_bot.via; }
  return out;
};
const assistantName = () => new URLSearchParams(location.search).get('name') || 'Assistant';
let ownerTimer = null;

// Take-over, as the server does it: wait for a running step, switch, hand the window over.
function takeover(action, fail) {
  if (action === 'take_over') {
    if (!state.enabled) throw fail(409, 'The browser is off in Settings');
    if (has('noscreen')) throw fail(409, 'No screen is available for the browser window');
    if (state.mode !== 'assistant') throw fail(409, 'The owner is already driving the browser');
    state.mode = state.agent === 'working' ? 'waiting' : 'switching';
    changed();
    if (state.mode === 'switching') setTimeout(ownerDrives, 900);
    return { ok: true };
  }
  if (action === 'stop_turn') {
    if (state.mode === 'waiting') { state.agent = 'idle'; state.holder = null; state.holder_bot = null; state.mode = 'switching'; changed(); setTimeout(ownerDrives, 900); }
    return { ok: true };
  }
  handBack();
  return { ok: true };
}

// A search met the person check (the mock's "search ..." turn); his window coming up, or a
// search that works, forgets it, as on the server.
export function personCheckMet() {
  state.person_check = true;
  changed();
}

function ownerDrives() {
  Object.assign(state, { person_check: false, mode: 'owner', owner_until: new Date(Date.now() + 15 * 60000).toISOString(), owner_refused: { private_network: 1 }, tabs: [] });
  changed();
  clearTimeout(ownerTimer);
  ownerTimer = setTimeout(handBack, 15 * 60000);
}

function handBack() {
  clearTimeout(ownerTimer);
  if ((has('restoring') || has('stuck')) && state.mode === 'owner') { // the server's clean-up after his window
    Object.assign(state, { mode: 'restoring', owner_until: null, owner_refused: {}, tabs: [],
      restore_problem: has('stuck') ? 'The browser profile is still in use' : null });
    changed();
    if (!has('stuck')) setTimeout(restored, 5000);
    return;
  }
  restored();
}

function restored() {
  Object.assign(state, { mode: 'assistant', owner_until: null, owner_refused: {}, restore_problem: null, tabs: state.running ? tabs() : [] });
  changed();
}
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
  if (!state.running || state.mode === 'owner' || !viewer.watching || viewer.waiting) return;
  viewer.waiting = true;
  seq += 1;
  viewer.emit({ type: 'browser_frame', tab: current + 1, seq, w: 960, h: 600, jpeg: paint() });
}

// The browser is used for a turn: it starts if needed, works for a while, then idles out.
// helper: the main assistant's web lookup holds the browser (v0.5.1), not a bot of its own
export function use(helper = false) {
  if (!state.enabled || mode === 'busy' || state.mode === 'restoring') return; // bots are refused while it cleans up
  clearTimeout(stopTimer);
  const holder_bot = helper ? { id: 'main', name: assistantName(), via: 'helper' } : { id: 'b-web', name: 'Web researcher', via: 'direct' };
  Object.assign(state, { running: true, agent: 'working', holder: 'conversation-1', holder_bot, tabs: tabs(), person_check: false });
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
  if (state.mode === 'restoring') return; // the clean-up isn't stoppable; it finishes by itself
  if (state.mode !== 'assistant') return handBack(); // stopping during a take-over hands back
  clearTimeout(stopTimer);
  const was = state.running;
  Object.assign(state, { running: false, agent: 'idle', holder: null, holder_bot: null, tabs: [], guard: { allowed: 0, refused: {}, background_refused: 0 } });
  if (was) tell({ type: 'browser_stopped', reason });
  changed();
}

setInterval(() => viewers.forEach(sendFrame), 220);

export function browserFetch(method, action, fail) {
  if (has('oldwindow')) throw fail(401, 'Open the window with `sasha window`'); // the server's own words
  if (has('forbidden')) throw fail(403, 'Loopback only');
  if (method === 'GET' && !action) return snapshot();
  if (method === 'POST' && ['take_over', 'hand_back', 'stop_turn'].includes(action)) return takeover(action, fail);
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
    if (mode === 'down' || has('oldwindow') || has('forbidden')) { // a refused handshake is 1006 too
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
