// How the Memory screen draws its parts. Note text and every server word go in as
// text (h() and textContent), never as markup: a note is whatever was said.
import { h, icon } from './dom.js';

const PLACE_HOME = 'on Home';
const PLACE_LISTENER = 'with the wake word';

export function when(seconds, now = Date.now()) {
  const m = Math.round((now - seconds * 1000) / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

// Where a note was made: by voice, in this window, or in a bot's chat (named from /api/bots).
export function where(note, botName) {
  if (note.source === 'wake') return h('span', { class: 'tag voice' }, 'said by voice');
  const name = botName(note.bot_id);
  if (note.source === 'chat' && name) return h('span', {}, `in the chat with ${name}`);
  return h('span', {}, note.source === 'window' ? 'in this window' : 'in a chat');
}

export function waitingRow(note, { botName, keep, remove, busy }) {
  return h('li', { class: 'note' },
    icon('mic'),
    h('span', { class: 'text' }, note.text),
    h('span', { class: 'meta' }, where(note, botName), h('span', {}, when(note.created_at))),
    h('span', { class: 'acts' },
      h('button', { class: 'btn small primary', type: 'button', disabled: busy, onclick: () => keep(note) }, icon('check'), 'Keep it'),
      h('button', { class: 'btn small', type: 'button', disabled: busy, onclick: () => remove(note) }, 'Delete')));
}

export function keptRow(note, { botName, asking, ask, remove, setAloud, busy }) {
  const box = h('input', { type: 'checkbox', checked: note.say_aloud, disabled: busy, onchange: (ev) => setAloud(note, ev.target.checked) });
  const row = h('li', { class: 'note' },
    icon('note'),
    h('span', { class: 'text' }, note.text),
    h('span', { class: 'meta' }, where(note, botName), h('span', {}, when(note.created_at))),
    h('span', { class: 'side' },
      h('label', { class: 'aloud', title: 'It may use this note when someone talks to it by the wake word' },
        h('span', { class: 'switch' }, box, h('span')), 'OK to say aloud'),
      h('button', { class: 'btn small quiet', type: 'button', 'aria-label': `Delete: ${note.text}`, disabled: busy, onclick: () => ask(note.id) }, icon('x'), 'Delete')));
  if (asking === note.id) {
    row.dataset.confirm = '';
    row.append(h('span', { class: 'sure', role: 'group', 'aria-label': 'Forget this note' },
      'Forget this note for good? It can’t be brought back.',
      h('button', { class: 'btn small danger solid', type: 'button', disabled: busy, onclick: () => remove(note) }, 'Forget it'),
      h('button', { class: 'btn small quiet', type: 'button', onclick: () => ask(null) }, 'Keep it')));
  }
  return row;
}

const PERIODS = [[5, '5 minutes'], [15, '15 minutes'], [60, '1 hour'], [1440, '1 day']];

export function forgetBox({ total, pending, confirmWindow, confirmAll, busy }) {
  const pick = h('select', { 'aria-label': 'How far back' }, PERIODS.map(([m, t]) => h('option', { value: String(m) }, t)));
  return h('div', { class: 'forget' },
    h('div', { class: 'row' },
      h('span', {}, 'Forget everything from the last'), pick,
      h('button', { class: 'btn', type: 'button', disabled: busy, onclick: () => confirmWindow(Number(pick.value), pick.selectedOptions[0].textContent) }, 'Forget…')),
    h('small', {}, 'Removes the notes made in that time, waiting ones too, and what was said in every conversation in that time, so it can’t be repeated.'),
    h('div', { class: 'row' },
      h('button', { class: 'btn danger', type: 'button', disabled: busy || total === 0, onclick: confirmAll }, `Forget all ${total} notes…`),
      h('small', {}, pending ? `Includes the ${pending} waiting for you. Nothing goes to a bin: forgotten notes are gone.` : 'Nothing goes to a bin: forgotten notes are gone.')));
}

// The server's own account of what went: the count, each note's text, and the turns per place.
export function reportBox(report, { what, botName }) {
  if (!report.count && !Object.keys(report.turns).length) {
    return h('div', { class: 'report none', role: 'status' }, h('b', {}, 'Nothing was forgotten.'), h('small', {}, `No notes were made ${what}.`));
  }
  const places = Object.entries(report.turns).map(([place, n]) => {
    const where2 = place === 'home' ? PLACE_HOME : place === 'listener' ? PLACE_LISTENER : `in the chat with ${botName(place) || 'a bot'}`;
    return `${n} ${n === 1 ? 'message' : 'messages'} ${where2}`;
  });
  return h('div', { class: 'report', role: 'status' },
    h('b', {}, !report.count ? `No notes were made ${what}.` : report.count === 1 ? `Forgot 1 note ${what}:` : `Forgot ${report.count} notes ${what}:`),
    report.items.length ? h('ul', {}, report.items.map((it) => h('li', {}, h('span', {}, it.text)))) : null,
    places.length ? h('small', {}, `Also removed: ${places.join(', ')}. Nothing else was changed.`) : null);
}

export function cleanReport(r) {
  const rep = r && typeof r === 'object' ? r : {};
  const items = Array.isArray(rep.items) ? rep.items.map((i) => ({ id: String(i?.id ?? ''), text: String(i?.text ?? '') })) : [];
  const turns = {};
  if (rep.turns && typeof rep.turns === 'object') {
    for (const [k, v] of Object.entries(rep.turns)) if (Number(v) > 0) turns[String(k)] = Number(v);
  }
  return { count: Number.isFinite(Number(rep.count)) ? Number(rep.count) : items.length, items, turns };
}
