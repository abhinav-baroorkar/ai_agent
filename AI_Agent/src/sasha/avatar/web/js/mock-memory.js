// Made-up notes for ?mock=1, shaped exactly as contract v0.6's Note, and a stand-in
// for the server's /api/memory routes and its `memory` and `forgot` frames. Nothing
// here is anyone's real note. Switches: &memory=off (a server without memory),
// &memory=empty, &memory=full (the cap reached).
const MODE = new URLSearchParams(location.search).get('memory') || '';
const MAX = 200;
const sockets = new Set();
const now = () => Date.now() / 1000;
let notes = null;

function seed() {
  const t = now();
  const note = (id, text, ago, source, bot_id, status, say_aloud) =>
    ({ id, text, created_at: Math.round(t - ago * 60), source, bot_id, lang: 'en', status, say_aloud });
  if (MODE === 'empty') return [];
  return [
    note('m-7', 'the bins go out on Tuesday night', 3, 'wake', 'voice', 'pending', false),
    note('m-6', 'I want the doors unlocked at night', 52, 'wake', 'voice', 'pending', false),
    note('m-5', 'I take the 8:10 bus on weekdays', 4, 'window', 'main', 'active', true),
    note('m-4', 'the boiler service is due in March', 60 * 26, 'wake', 'voice', 'active', true),
    note('m-3', 'I am allergic to peanuts', 60 * 24 * 3, 'chat', 'b-recipes', 'active', false),
    note('m-2', 'the spare key is in the blue box in the garage', 60 * 24 * 6, 'chat', 'main', 'active', false),
    // a note is the owner's words as said: the page must show markup as plain text
    note('m-1', 'the plants <img src=x onerror=alert(1)> need water on <b>Sunday</b>', 60 * 24 * 9, 'chat', 'b-mail', 'active', false),
  ];
}
const all = () => (notes ??= seed());
const copy = (x) => JSON.parse(JSON.stringify(x));
const snapshot = () => ({ notes: copy(all()), count: all().length, max: MODE === 'full' ? all().length : MAX });

function changed() {
  for (const s of sockets) s.emit({ type: 'memory', ...snapshot() });
}

export function attach(socket) {
  if (MODE === 'off') return;
  sockets.add(socket);
  setTimeout(() => socket.emit({ type: 'memory', ...snapshot() }), 220);
}
export const detach = (socket) => sockets.delete(socket);

export function memoryFetch(method, id, action, body, fail) {
  if (MODE === 'off') throw fail(404, 'Not Found');
  if (method === 'GET' && !id) return snapshot();
  if (method === 'POST' && id === 'forget') return forget(body || {}, fail);
  const n = all().find((x) => x.id === id);
  if (!n) throw fail(404, 'That note no longer exists.');
  if (method === 'POST' && action === 'keep') {
    n.status = 'active';
    n.say_aloud = true;
    changed();
    return copy(n);
  }
  if (method === 'PATCH') {
    if (n.status === 'pending') throw fail(409, 'Keep this note first.');
    if (typeof body?.say_aloud !== 'boolean') throw fail(400, 'Only say_aloud (true or false) can change');
    n.say_aloud = body.say_aloud;
    changed();
    return copy(n);
  }
  if (method === 'DELETE') {
    notes = all().filter((x) => x !== n);
    changed();
    return { ok: true };
  }
  throw fail(404, 'Not found.');
}

function forget(body, fail) {
  if (body.everything === true) {
    const items = all().map((n) => ({ id: n.id, text: n.text }));
    notes = [];
    changed();
    return { count: items.length, items, turns: {} };
  }
  const minutes = body.minutes;
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) throw fail(400, 'minutes is out of range');
  const since = now() - minutes * 60;
  const gone = all().filter((n) => n.created_at >= since);
  notes = all().filter((n) => n.created_at < since);
  for (const s of sockets) s.emit({ type: 'forgot', since_s: minutes * 60 });
  changed();
  // The mock keeps no real history: it reports a made-up count for Home's turns.
  return { count: gone.length, items: gone.map((n) => ({ id: n.id, text: n.text })), turns: { home: 2 } };
}
