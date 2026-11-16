// Choosing how a bot's orb breathes: one live orb per style in the bot's own
// colour, and a bigger preview of the chosen one that can show each state, so the
// owner can see how it will look while it listens, thinks and talks.
import { h, clear } from './dom.js';
import { STYLES, STYLE_IDS } from './faces/styles.js';
import { miniOrb, setOrb } from './faces/mini-orb.js';

const STATES = [['idle', 'Resting'], ['listening', 'Listening'], ['thinking', 'Thinking'], ['speaking', 'Talking']];

// color: [r, g, b]; value: the style id now chosen; onPick(id) when he picks another.
export function stylePicker({ color, value, onPick }) {
  let state = 'idle';
  const big = miniOrb(color, 150, { style: value, state });
  const name = h('b', {}, STYLES[value].name);
  const line = h('p', {}, STYLES[value].line);
  const chips = h('div', { class: 'state-chips', role: 'radiogroup', 'aria-label': 'Show it' });
  function drawChips() {
    clear(chips).append(...STATES.map(([id, label]) => h('button', {
      type: 'button', role: 'radio', 'aria-checked': String(state === id), class: 'chip-btn',
      onclick: () => { state = id; setOrb(big, { state }); drawChips(); },
    }, label)));
  }
  drawChips();
  const grid = h('div', { class: 'styles', role: 'radiogroup', 'aria-label': 'How it breathes' }, ...STYLE_IDS.map((id) => h('button', {
    type: 'button', role: 'radio', class: 'style-opt', 'aria-checked': String(id === value), title: STYLES[id].line,
    onclick: () => {
      value = id;
      setOrb(big, { style: id });
      name.textContent = STYLES[id].name;
      line.textContent = STYLES[id].line;
      grid.querySelectorAll('.style-opt').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.id === id)));
      onPick(id);
    },
    dataset: { id },
  }, miniOrb(color, 64, { style: id }), h('span', {}, STYLES[id].name))));
  return h('div', { class: 'picker' }, grid,
    h('div', { class: 'preview' }, big, name, line, chips,
      h('p', { class: 'hint' }, 'Every style shows listening, thinking and talking the same way, so you can always tell what it is doing.')));
}
