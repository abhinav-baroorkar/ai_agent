// The timers card on Home and the ringing bar. Items are made by talking or
// typing to the assistant; this only lists, counts down, stops, snoozes,
// reads aloud and deletes them.
//
// Privacy: a reminder's text is the owner's. It is shown as plain text on the
// card and nowhere else: never in the page title, an address or the console.
import { api, MOCK } from './api.js';
import { $, clear } from './dom.js';
import { cleanTimer } from './clean.js';
import { NAME, timerRow } from './timers-view.js';

const CONFIRM_MS = 4000; // how long "Really delete?" stays armed
const ESC_SNOOZE_MIN = 5; // what Escape does to a ringing alarm
const ORDER = { ringing: 0, pending: 1, scheduled: 2, missed: 3, done: 4 };

export function initTimers({ conv, onRinging }) {
  const el = {
    card: $('#timers'), list: $('#timer-list'), empty: $('#timers-empty'), done: $('#timers-done'),
    doneList: $('#timer-done-list'), error: $('#timers-error'), mock: $('#mock-ring'),
    bar: $('#ring-bar'), barText: $('#ring-text'), barIcon: $('#ring-icon'), barStop: $('#ring-stop'),
    barHint: $('#ring-hint'),
  };
  let items = [];
  let offset = 0; // server clock minus this browser's clock, in ms
  let armed = null; // id of the item whose Delete was pressed once
  let ticking = [];
  const says = new Map(); // id -> the sentence the server is speaking for a ringing item
  const serverNow = () => Date.now() + offset;

  function fail(err) {
    el.error.hidden = !err;
    el.error.textContent = err ? String(err.message || err) : '';
  }

  function apply(now, list) {
    const stamp = Date.parse(now);
    if (Number.isFinite(stamp)) offset = stamp - Date.now();
    items = (Array.isArray(list) ? list : []).map(cleanTimer).filter(Boolean);
    items.sort((a, b) => ORDER[a.state] - ORDER[b.state] || a.due - b.due);
    for (const id of says.keys()) if (!items.some((t) => t.id === id && t.state === 'ringing')) says.delete(id);
    el.card.hidden = false;
    render();
  }

  // Asked on start, when the connection returns and when the tab wakes: after a
  // sleep the local clock and the list may both be stale.
  async function load() {
    try {
      const r = await api('GET', '/api/timers');
      fail(null);
      apply(r.now, r.timers);
    } catch {
      if (!items.length) el.card.hidden = true; // a server without timers: show nothing at all
    }
  }

  async function act(method, t, action, body) {
    try {
      fail(null);
      await api(method, `/api/timers/${encodeURIComponent(t.id)}${action ? `/${action}` : ''}`, body);
    } catch (err) {
      fail(err);
    }
  }

  const actions = {
    stop: (t) => (conv.post({ type: 'timer_dismiss', id: t.id }) ? null : act('POST', t, 'dismiss')),
    snooze: (t, minutes) => (conv.post({ type: 'timer_snooze', id: t.id, minutes }) ? null : act('POST', t, 'snooze', { minutes })),
    dismiss: (t) => act('POST', t, 'dismiss'),
    resnooze: (t, minutes) => act('POST', t, 'snooze', { minutes }), // a missed item, by HTTP
    say: (t) => act('POST', t, 'say'),
    remove(t) { // two clicks: the first arms it for a few seconds, the second deletes
      if (armed !== t.id) {
        armed = t.id;
        render();
        setTimeout(() => { if (armed === t.id) { armed = null; render(); } }, CONFIRM_MS);
        return;
      }
      armed = null;
      act('DELETE', t);
    },
  };

  function render() {
    const focused = document.activeElement && document.activeElement.dataset.key;
    const ctx = { serverNow, armed, actions };
    const live = items.filter((t) => t.state !== 'done');
    const done = items.filter((t) => t.state === 'done');
    const rows = live.map((t) => timerRow(t, ctx));
    clear(el.list).append(...rows.map((r) => r.el));
    clear(el.doneList).append(...done.map((t) => timerRow(t, ctx).el));
    ticking = rows.filter((r) => r.tick);
    el.done.hidden = !done.length;
    el.done.querySelector('summary').textContent = `Done (${done.length})`;
    el.empty.hidden = items.length > 0;
    el.card.dataset.empty = String(items.length === 0);
    if (focused) {
      const again = el.card.querySelector(`[data-key="${CSS.escape(focused)}"]`);
      if (again) again.focus();
    }
    renderBar();
    tick();
  }

  function tick() {
    if (!document.hidden) ticking.forEach((r) => r.tick());
  }

  // The bar across every screen while something rings.
  function renderBar() {
    const ringing = items.filter((t) => t.state === 'ringing');
    const first = ringing[0];
    const was = !el.bar.hidden;
    el.bar.hidden = !first;
    onRinging(!!first);
    if (!first) return;
    // Without the server's sentence (the page opened mid-ring) the bar names only the kind.
    // It never builds a sentence from the item's own text: what is shown here is what the
    // server chose to say, and for late items that is deliberately a generic line.
    const fallback = `${first.kind === 'alarm' ? 'An' : 'A'} ${NAME[first.kind].toLowerCase()} is ringing.`;
    const more = ringing.length > 1 ? `  (+${ringing.length - 1} more)` : '';
    el.barText.textContent = (says.get(first.id) || fallback) + more;
    el.barIcon.setAttribute('href', `#i-${first.kind === 'alarm' ? 'alarm' : first.kind === 'reminder' ? 'note' : 'timer'}`);
    el.bar.dataset.id = first.id;
    el.barHint.textContent = first.kind === 'alarm'
      ? `Esc here snoozes ${ESC_SNOOZE_MIN} minutes. Only the Stop button turns an alarm off.`
      : 'Esc here stops it.';
    // take the keyboard only if nobody is using it
    if (!was && (document.activeElement === document.body || !document.activeElement)) el.barStop.focus();
  }

  const ringingNow = () => items.find((t) => t.id === el.bar.dataset.id && t.state === 'ringing');
  el.barStop.addEventListener('click', () => { const t = ringingNow(); if (t) actions.stop(t); });
  el.bar.querySelectorAll('[data-snooze]').forEach((b) => b.addEventListener('click', () => {
    const t = ringingNow();
    if (t) actions.snooze(t, Number(b.dataset.snooze));
  }));
  // Escape acts on the ring only when the keyboard is on the bar, or on nothing at
  // all. From a text box, the history panel or anywhere else it keeps its usual
  // meaning (stop the answer, clear, close), so it can't silence an alarm by accident.
  // On an alarm it only snoozes: dismissing a wake-up alarm takes the Stop button.
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || el.bar.hidden) return;
    const active = document.activeElement;
    const onBar = el.bar.contains(active);
    const onNothing = !active || active === document.body;
    if (!onBar && !onNothing) return;
    const t = ringingNow();
    if (!t) return;
    ev.stopImmediatePropagation();
    ev.preventDefault();
    if (t.kind === 'alarm') actions.snooze(t, ESC_SNOOZE_MIN);
    else actions.stop(t);
  }, true);

  conv
    .on('timers', (m) => apply(m.now, m.timers))
    .on('timer_ringing', (m) => {
      says.set(String(m.id), String(m.say ?? ''));
      const t = items.find((x) => x.id === String(m.id));
      if (t) t.state = 'ringing';
      render();
    })
    .on('timer_stopped', (m) => {
      says.delete(String(m.id));
      const t = items.find((x) => x.id === String(m.id));
      if (t && t.state === 'ringing') t.state = m.how === 'snoozed' ? 'scheduled' : 'done'; // the snapshot that follows has the details
      render();
    })
    .on('online', load);

  setInterval(tick, 500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  if (MOCK) {
    el.mock.hidden = false;
    el.mock.addEventListener('click', async () => (await import('./mock-timers.js')).ring());
  }
  load();
}
