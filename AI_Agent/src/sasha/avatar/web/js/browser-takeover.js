// Taking over the assistant's browser (contract v0.5): the owner drives it himself
// in a normal window to solve the search engine's person check, then hands it back. The
// brain can't see that window, so this part only offers the switch, says what is
// happening and counts down the time left. It appears only when the server's
// browser state says which mode it is in.
import { api } from './api.js';
import { h, clear, icon } from './dom.js';

// Not an invitation to sign in: sign-ins are out of scope, and only the search engine's
// cookies survive the hand-back (design §9.7, C3).
const WHY = 'Solve the search engine’s person check yourself. Don’t sign in to accounts here: everything except that check is wiped when you hand back.';
// The server's refusals, in plain words. Anything else is shown as it comes.
const PLAIN = [
  [/off in Settings/i, 'The browser is off. Turn it on in Settings first.'],
  [/already driving/i, 'You already have the browser open in its own window.'],
  [/No screen/i, 'There’s no screen here to open the browser window on.'],
  [/not installed/i, 'Chrome isn’t installed on this computer, so there’s no browser window to open.'],
];
const plain = (m) => (PLAIN.find(([re]) => re.test(m)) || [null, m])[1];

function left(until) {
  const s = Math.max(0, Math.ceil((until - Date.now()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function initTakeover(root, health) {
  let state = null;
  let error = '';
  let busy = false;
  let timer = null;

  async function post(action) {
    busy = true;
    error = '';
    draw();
    try {
      await api('POST', `/api/browser/${action}`);
    } catch (err) {
      error = plain(String(err.message || err));
    }
    busy = false;
    draw();
  }

  function draw() {
    clearInterval(timer);
    const mode = state && state.mode;
    root.hidden = !mode;
    if (!mode) return;
    const who = state.user || health.assistant.name;
    const parts = [];
    if (mode === 'assistant') {
      parts.push(h('div', { class: 'to-row' },
        h('button', { class: 'btn small', type: 'button', disabled: busy || !state.enabled, onclick: () => post('take_over') }, 'Take over'),
        h('p', {}, WHY)));
    } else if (mode === 'waiting') {
      parts.push(h('div', { class: 'to-row' },
        h('p', {}, `Waiting for ${who} to finish what it is doing, then the browser is yours.`),
        h('button', { class: 'btn small danger', type: 'button', disabled: busy, onclick: () => post('stop_turn') }, 'Stop it now')));
    } else if (mode === 'switching') {
      parts.push(h('p', { class: 'to-note' }, 'Opening the browser in its own window…'));
    } else if (mode === 'owner') {
      const count = h('b', { class: 'count' });
      const tick = () => { count.textContent = state.owner_until ? left(state.owner_until) : ''; };
      tick();
      timer = setInterval(tick, 1000);
      const refused = Object.values(state.owner_refused).reduce((a, b) => a + b, 0);
      parts.push(h('div', { class: 'to-row' },
        h('p', {}, 'You’re driving: the browser is open in its own window. The assistant can’t see it or use it until you hand it back.',
          state.owner_until && h('span', { class: 'to-left' }, ' It comes back by itself in ', count, '.')),
        h('button', { class: 'btn small primary', type: 'button', disabled: busy, onclick: () => post('hand_back') }, icon('back'), 'Hand back')));
      if (refused) parts.push(h('p', { class: 'to-note' }, `${refused} ${refused === 1 ? 'address' : 'addresses'} refused in your window.`));
    }
    if (error) parts.push(h('p', { class: 'form-error', role: 'alert' }, error));
    clear(root).append(...parts);
  }

  return {
    show(next) {
      state = next;
      draw();
    },
  };
}
