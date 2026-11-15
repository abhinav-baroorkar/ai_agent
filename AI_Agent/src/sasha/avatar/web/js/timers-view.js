// How one timer, alarm or reminder looks on the card. Everything from the
// server goes in as plain text.
import { h, icon, setText } from './dom.js';

export const NAME = { timer: 'Timer', alarm: 'Alarm', reminder: 'Reminder' };
const ICON = { timer: 'timer', alarm: 'alarm', reminder: 'note' };
const REPEAT = { daily: 'Every day', weekdays: 'Weekdays', weekends: 'Weekends' };
const DAY_MS = 86400000;
const pad = (n) => String(n).padStart(2, '0');

export const titleOf = (t) => (t.kind === 'reminder' ? t.text || t.label : t.label) || NAME[t.kind];

export function remaining(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  return hours ? `${hours}:${pad(mins)}:${pad(s % 60)}` : `${mins}:${pad(s % 60)}`;
}

const clock = (ms) => new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const midnight = (ms) => new Date(ms).setHours(0, 0, 0, 0);

// "today" is left out; other days are named relative to the server's now.
export function dayOf(ms, now) {
  const days = Math.round((midnight(ms) - midnight(now)) / DAY_MS);
  if (days === 0) return '';
  if (days === 1) return 'tomorrow';
  if (days === -1) return 'yesterday';
  return new Date(ms).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
}

export function lateBy(seconds) {
  const mins = Math.round(seconds / 60);
  if (mins < 1) return 'late';
  return mins < 60 ? `${mins} min late` : `${Math.round(mins / 60)} h late`;
}

function button(t, key, label, onClick, cls = '') {
  return h('button', { class: `btn small ${cls}`, type: 'button', dataset: { key: `${t.id}:${key}` }, onclick: onClick }, label);
}

// Returns { el, tick }: tick (when present) refreshes the countdown text.
export function timerRow(t, { serverNow, armed, actions }) {
  const what = h('div', { class: 'what' });
  setText(what, titleOf(t));
  const when = h('div', { class: 'when' });
  const acts = h('div', { class: 'acts' });
  const side = h('span', { class: 'side' }); // the quiet Delete, beside the title
  const at = [clock(t.due), dayOf(t.due, serverNow())].filter(Boolean).join(' ');
  let tick = null;

  if (t.state === 'scheduled' && t.kind === 'timer') {
    const count = h('span', { class: 'count', role: 'timer', 'aria-label': 'Time left' });
    tick = () => { count.textContent = remaining(t.due - serverNow()); };
    when.append(count, h('span', {}, `until ${at}`));
  } else if (t.state === 'scheduled') {
    when.append(h('span', { class: 'count' }, clock(t.due)), dayOf(t.due, serverNow()));
  } else if (t.state === 'ringing') {
    when.append(h('strong', {}, 'Ringing now'));
  } else if (t.state === 'pending') {
    when.append(`Came due at ${at}`);
  } else {
    when.append(`${t.state === 'missed' ? 'Missed' : 'Done'} · ${at}`);
  }
  if (t.repeat !== 'none') when.append(h('span', { class: 'tag' }, REPEAT[t.repeat]));
  if (t.late_s > 0 && t.state !== 'done') when.append(h('span', { class: 'tag late' }, lateBy(t.late_s)));
  if (t.voice) when.append(h('span', { class: 'tag voice', title: 'Made by voice' }, 'voice'));

  const readable = t.kind === 'reminder' && t.text;
  if (t.state === 'ringing') {
    acts.append(button(t, 'stop', 'Stop', () => actions.stop(t), 'primary'),
      ...[5, 10, 15].map((m) => button(t, `snooze${m}`, `Snooze ${m} min`, () => actions.snooze(t, m))));
  } else if (t.state === 'pending') {
    if (readable) acts.append(button(t, 'say', 'Read it aloud', () => actions.say(t)));
    acts.append(button(t, 'dismiss', 'Dismiss', () => actions.dismiss(t)));
  } else {
    if (readable && t.state === 'missed') acts.append(button(t, 'say', 'Read it aloud', () => actions.say(t)));
    const word = t.state === 'scheduled' ? 'Delete' : 'Remove';
    const sure = armed === t.id;
    side.append(button(t, 'remove', sure ? 'Really?' : word, () => actions.remove(t), sure ? 'danger' : 'quiet'));
  }

  const el = h('li', { class: 'timer', dataset: { state: t.state } }, icon(ICON[t.kind]),
    h('div', {}, h('span', { class: 'sr' }, `${NAME[t.kind]}: `), what), side, when, acts);
  return { el, tick };
}
