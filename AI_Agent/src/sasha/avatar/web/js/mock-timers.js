// Made-up timers, alarms and reminders for ?mock=1, one in every state, and a
// stand-in for the server's timer behaviour. All times are built from the
// browser's own clock when the page loads; nothing here is a real plan of anyone's.
const MIN = 60000;
// ?mock=1&skew=3600 puts the mock server's clock an hour ahead of the browser's, to
// check that countdowns follow the server's `now` and not the local clock.
const SKEW = (Number(new URLSearchParams(location.search).get('skew')) || 0) * 1000;
const clock = () => Date.now() + SKEW;
const sockets = new Set();
let items = null;

// ISO 8601 local time with offset, the format the real server uses.
export function iso(ms) {
  const d = new Date(ms);
  const off = -d.getTimezoneOffset();
  const pad = (n) => String(Math.trunc(Math.abs(n))).padStart(2, '0');
  const local = new Date(ms + off * MIN).toISOString().slice(0, 19);
  return `${local}${off < 0 ? '-' : '+'}${pad(off / 60)}:${pad(off % 60)}`;
}

function seed() {
  const now = clock();
  const tomorrow = new Date(now + 24 * 60 * MIN);
  tomorrow.setHours(6, 30, 0, 0);
  const item = (id, kind, state, due, extra = {}) => ({
    id, kind, state, due_at: iso(due), label: '', text: '', repeat: 'none', late_s: 0, snoozes: 0, created_by: 'chat', ...extra,
  });
  return [
    item('t-tea', 'timer', 'scheduled', now + 4 * MIN + 12000, { label: 'Tea' }),
    item('t-wash', 'timer', 'scheduled', now + 47 * MIN, { label: 'Laundry', created_by: 'wake' }),
    item('t-call', 'reminder', 'scheduled', now + 135 * MIN, { text: 'Call the dentist about the check-up', created_by: 'wake' }),
    item('t-wake', 'alarm', 'scheduled', tomorrow.getTime(), { label: 'Wake up', repeat: 'weekdays' }),
    item('t-plants', 'reminder', 'pending', now - 22 * MIN, { text: 'Water the plants on the balcony', late_s: 1320 }),
    item('t-oven', 'alarm', 'missed', now - 5 * 60 * MIN, { label: 'Oven check' }),
    item('t-bins', 'reminder', 'missed', now - 26 * 60 * MIN, { text: 'Put the bins out', late_s: 0 }),
    item('t-eggs', 'timer', 'done', now - 3 * 60 * MIN, { label: 'Eggs' }),
  ];
}

const all = () => (items ||= seed());
const snapshot = () => ({ now: iso(clock()), timers: JSON.parse(JSON.stringify(all())) });
const find = (id) => all().find((t) => t.id === id);

function broadcast(msg) {
  for (const s of sockets) s.emit(msg);
}

function changed() {
  broadcast({ type: 'timers', ...snapshot() });
}

function sayFor(t) {
  const voice = t.created_by === 'wake' ? 'Voice ' : '';
  if (t.state === 'pending' || t.late_s > 0) return 'You have 1 missed reminder.';
  if (t.kind === 'reminder') return `${voice ? 'Voice reminder' : 'Reminder'}: ${t.text}`;
  return `${voice}${t.kind === 'alarm' ? 'alarm' : 'timer'}${t.label ? `: ${t.label}` : ''}.`.replace(/^./, (c) => c.toUpperCase());
}

// Makes an item ring now, as the server would when it comes due.
export function ring(id) {
  const t = find(id) || all().find((x) => x.state === 'scheduled');
  if (!t) return;
  t.state = 'ringing';
  t.due_at = iso(clock());
  changed();
  const say = sayFor(t);
  broadcast({ type: 'timer_ringing', id: t.id, kind: t.kind, created_by: t.created_by, say });
  for (const s of sockets) if (s.speaks) s.speak(say).then(() => s.emit({ type: 'state', state: 'idle' }));
}

function stop(id, how, minutes = 0) {
  const t = find(id);
  if (!t) return null;
  if (how === 'snoozed') {
    t.state = 'scheduled';
    t.snoozes += 1;
    t.due_at = iso(clock() + minutes * MIN);
  } else {
    t.state = 'done';
  }
  broadcast({ type: 'timer_stopped', id, how });
  changed();
  return t;
}

// The socket side: a snapshot on connect, and the two client frames.
export function attach(socket) {
  sockets.add(socket);
  setTimeout(() => socket.emit({ type: 'timers', ...snapshot() }), 200);
}
export const detach = (socket) => sockets.delete(socket);

export function onFrame(msg) {
  if (msg.type === 'timer_dismiss') stop(msg.id, 'dismissed');
  if (msg.type === 'timer_snooze') stop(msg.id, 'snoozed', Number(msg.minutes) || 5);
}

// The HTTP side: /api/timers and its routes. Returns undefined if the path is not ours.
export function timersFetch(method, id, action, body, fail) {
  if (!id && method === 'GET') return snapshot();
  const t = find(id);
  if (!t) throw fail(404, 'That item no longer exists.');
  if (method === 'DELETE') {
    all().splice(all().indexOf(t), 1);
    broadcast({ type: 'timer_stopped', id, how: 'cancelled' });
    changed();
    return { ok: true };
  }
  if (action === 'dismiss') return stop(id, 'dismissed');
  if (action === 'snooze') {
    const minutes = Number(body && body.minutes);
    if (!(minutes >= 1 && minutes <= 60)) throw fail(400, 'Snooze takes 1 to 60 minutes.');
    return stop(id, 'snoozed', minutes);
  }
  if (action === 'say') {
    for (const s of sockets) if (s.speaks) s.speak(t.text || t.label || 'Timer').then(() => s.emit({ type: 'state', state: 'idle' }));
    return { ok: true };
  }
  throw fail(404, 'Not found.');
}
