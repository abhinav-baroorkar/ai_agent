// Memory (contract v0.6): the list of notes the owner asked the assistant to keep,
// and a card on Home when a note made by voice waits to be kept. Inert until the
// server answers GET /api/memory (or sends a `memory` frame): before that there is
// no Memory link and no card. Notes are made and forgotten by talking; this screen
// keeps, marks, deletes and forgets, and shows exactly what the server removed.
import { api } from './api.js';
import { $, h, clear } from './dom.js';
import { cleanMemory } from './clean.js';
import { waitingRow, keptRow, forgetBox, reportBox, cleanReport } from './memory-view.js';

export function initMemory({ conv, history, bots, onReady }) {
  const el = {
    pane: $('#memory'), link: $('#to-memory'), card: $('#memory-card'), cardText: $('#memory-card-text'),
    cardMore: $('#memory-card-more'), dialog: $('#memory-confirm'),
  };
  let state = { notes: [], count: 0, max: 200 };
  let supported = false;
  let asking = null; // the note asking "forget for good?"
  let busy = false;
  let report = null; // the last forget's report, until the screen is left
  let error = '';
  let names = new Map();

  bots.onChange((list) => { names = new Map(list.map((b) => [b.id, b.name])); render(); });
  const botName = (id) => names.get(id) || '';
  const pending = () => state.notes.filter((n) => n.pending);
  const kept = () => state.notes.filter((n) => !n.pending);

  function apply(raw) {
    state = cleanMemory(raw);
    if (asking && !state.notes.some((n) => n.id === asking)) asking = null;
    const first = !supported;
    supported = true;
    render();
    if (first) onReady(); // the Memory link and route exist from now on
  }

  async function load() {
    try {
      apply(await api('GET', '/api/memory'));
    } catch (err) {
      if (supported) { error = err.message; render(); } // a server without memory stays quiet
    }
  }

  async function act(fn) {
    if (busy) return;
    busy = true;
    error = '';
    render();
    try {
      await fn();
    } catch (err) {
      error = err.message;
    }
    busy = false;
    await load(); // the `memory` frame says the same; this keeps the screen right without it
  }

  const id = (n) => encodeURIComponent(n.id);
  const keep = (n) => act(() => api('POST', `/api/memory/${id(n)}/keep`, {}));
  const remove = (n) => act(async () => { await api('DELETE', `/api/memory/${id(n)}`); asking = null; });
  const setAloud = (n, on) => act(() => api('PATCH', `/api/memory/${id(n)}`, { say_aloud: on }));
  const ask = (noteId) => { asking = noteId; render(); };

  // Both forgets are confirmed first (the contract asks the page to).
  function confirm(title, text, yes, run) {
    el.dialog.querySelector('h2').textContent = title;
    el.dialog.querySelector('p').textContent = text;
    el.dialog.querySelector('[value="yes"]').textContent = yes;
    el.dialog.onclose = () => { if (el.dialog.returnValue === 'yes') run(); };
    el.dialog.returnValue = '';
    el.dialog.showModal();
  }
  function forget(body, what) {
    return act(async () => { report = { what, ...cleanReport(await api('POST', '/api/memory/forget', body)) }; });
  }
  const confirmWindow = (minutes, label) => confirm(
    `Forget the last ${label}?`,
    `The notes made in the last ${label} go, waiting ones too, and so does everything said in that time in every conversation. It can’t be undone.`,
    'Forget', () => forget({ minutes }, `from the last ${label}`));
  const confirmAll = () => confirm(
    `Forget all ${state.notes.length} notes?`,
    `Every note goes${pending().length ? `, the ${pending().length} waiting for you too` : ''}. They can’t be brought back, and the assistant won’t know any of them afterwards.`,
    'Forget everything', () => forget({ everything: true }, ''));

  function render() {
    el.link.hidden = !supported || location.hash === '#memory';
    renderCard();
    if (el.pane.hidden) return;
    const full = state.count >= state.max;
    const kids = [
      h('div', { class: 'mem-head' }, h('h1', {}, 'Memory'), h('span', { class: `usage${full ? ' full' : ''}` }, `${state.count} of ${state.max} notes`)),
      h('p', { class: 'lede' }, 'What you asked the assistant to remember, in your own words. It uses these notes to answer you; a note never makes it do anything.'),
    ];
    if (error) kids.push(h('p', { class: 'form-error', role: 'alert' }, error));
    if (report) kids.push(reportBox(report, { what: report.what, botName }));
    if (full) kids.push(h('p', { class: 'form-note' }, 'Your notes are full. New ones are refused until you forget some; none are removed on their own.'));
    if (!state.notes.length) {
      kids.push(h('div', { class: 'mem-empty' }, h('b', {}, 'Nothing remembered yet.'),
        h('span', {}, 'Say or type something like ', h('em', { class: 'say-it' }, '“remember that the bins go out on Tuesday”'), '. It shows up here, and you can delete it at any time.')));
    }
    const opts = { botName, keep, remove, setAloud, ask, asking, busy };
    if (pending().length) {
      kids.push(
        h('h2', { class: 'eyebrow' }, 'Waiting for you', h('span', { class: 'count' }, String(pending().length))),
        h('p', { class: 'why' }, 'Added by voice. Anyone near the speaker could have said these, a TV too, so they aren’t used until you keep them.'),
        h('ul', { class: 'mem-list waiting' }, pending().map((n) => waitingRow(n, opts))));
    }
    if (kept().length) {
      kids.push(
        h('h2', { class: 'eyebrow' }, 'Remembered', h('span', { class: 'count' }, String(kept().length))),
        h('p', { class: 'why' }, '“OK to say aloud” lets it use a note when someone talks to it by the wake word. The others are used only in this window and in your chats.'),
        h('ul', { class: 'mem-list' }, kept().map((n) => keptRow(n, opts))));
    }
    if (state.notes.length) {
      kids.push(h('h2', { class: 'eyebrow' }, 'Forget'),
        forgetBox({ total: state.notes.length, pending: pending().length, confirmWindow, confirmAll, busy }));
    }
    const top = el.pane.scrollTop;
    clear(el.pane).append(...kids);
    el.pane.scrollTop = top;
  }

  // Home: the newest note made by voice, with keep and delete in reach.
  function renderCard() {
    const wait = pending();
    el.card.hidden = !supported || !wait.length;
    if (el.card.hidden) return;
    const note = wait[0]; // newest first
    el.cardText.textContent = note.text;
    el.cardMore.hidden = wait.length < 2;
    el.cardMore.textContent = wait.length === 2 ? 'and 1 more' : `and ${wait.length - 1} more`;
    el.card.querySelector('[data-act="keep"]').onclick = () => keep(note);
    el.card.querySelector('[data-act="delete"]').onclick = () => remove(note);
    for (const b of el.card.querySelectorAll('button')) b.disabled = busy;
  }

  // `forgot`: the server already dropped every conversation's turns from the window;
  // Home drops its own on-screen ones (chats do theirs in chat.js).
  function forgotOnScreen(sinceS) {
    if (!(sinceS > 0)) return;
    if (history.forget(Date.now() - sinceS * 1000)) {
      $('#heard').textContent = '';
      $('#said').textContent = '';
    }
  }

  conv.on('memory', apply).on('forgot', (m) => forgotOnScreen(Number(m.since_s)));
  load();
  return {
    get supported() { return supported; },
    show() { report = null; asking = null; error = ''; load(); render(); },
    leave() { report = null; asking = null; },
  };
}
