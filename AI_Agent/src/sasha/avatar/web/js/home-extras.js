// The quieter parts of Home: the corner readouts, the history drawer, and
// knowing whether the browser has blocked the microphone.
import { api } from './api.js';
import { $, h, icon, setText } from './dom.js';
import { cleanBot } from './clean.js';

// Small facts in the corners: local time, the model, the link, the last answer's speed.
export function initReadouts(health) {
  const el = { time: $('#r-time'), model: $('#r-model'), link: $('#r-link'), last: $('#r-last') };
  const tick = () => {
    const now = new Date();
    const day = now.toLocaleDateString([], { weekday: 'short' });
    el.time.textContent = `${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · ${day}`;
  };
  tick();
  setInterval(tick, 10000);

  async function loadModel() {
    try {
      const main = ((await api('GET', '/api/bots')).bots || []).map(cleanBot).find((b) => b.id === health.default_bot);
      el.model.textContent = main ? main.model : 'unknown';
    } catch {
      el.model.textContent = 'unknown';
    }
  }
  loadModel();
  window.addEventListener('hashchange', () => { if (!$('#home').hidden) loadModel(); });

  return {
    link(online) {
      el.link.textContent = online ? 'Connected' : 'Offline';
      el.link.dataset.tone = online ? 'ok' : 'bad';
    },
    answered(timing) {
      if (!timing || timing.total_ms == null) return;
      const speed = timing.tokens_per_s ? ` · ${Math.round(timing.tokens_per_s)} tokens/s` : '';
      const secs = Number(timing.total_ms) / 1000;
      if (Number.isFinite(secs)) el.last.textContent = `${secs.toFixed(1)} s${speed}`;
    },
  };
}

// Everything said in this conversation, newest at the bottom. Kept in memory
// only: it is gone when the window closes or the conversation is reset.
export function initHistory(name) {
  const el = { open: $('#history-open'), close: $('#history-close'), panel: $('#history'), list: $('#history-list'), empty: $('#history-empty') };
  let turn = null;

  function show(on) {
    el.panel.hidden = !on;
    el.open.setAttribute('aria-expanded', String(on));
    if (on) {
      el.list.scrollTop = el.list.scrollHeight;
      el.close.focus();
    } else {
      el.open.focus();
    }
  }
  el.open.addEventListener('click', () => show(el.panel.hidden));
  el.close.addEventListener('click', () => show(false));
  el.panel.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); show(false); } });

  function current() {
    if (turn) return turn;
    const heard = h('p', { class: 'h-heard' });
    const chips = h('div', { class: 'h-chips' });
    const said = h('p', { class: 'h-said' });
    el.list.append(h('li', {}, heard, chips, h('div', {}, h('strong', { class: 'eyebrow' }, name), said)));
    el.empty.hidden = true;
    turn = { heard, chips, said };
    return turn;
  }

  return {
    heard(text) {
      turn = null;
      setText(current().heard, text);
    },
    tool(tool) {
      current().chips.append(h('span', { class: `chip ${tool.ok ? 'ok' : 'bad'}` }, icon(tool.ok ? 'check' : 'x'), String(tool.name ?? 'tool')));
    },
    said(text) { setText(current().said, text); },
    stopped() { if (turn && turn.said.textContent) turn.said.dataset.stopped = ''; },
    get isOpen() { return !el.panel.hidden; },
  };
}

// What Home shows of a spoken answer: plain words (formatting marks dropped, this is
// not a renderer), and for a long answer only its end, starting at a sentence or at
// least a word, so the lines never begin in the middle of something.
export function spokenTail(text, budget) {
  const clean = String(text)
    .replace(/```[\s\S]*?(```|$)/g, ' ')
    .replace(/^\s{0,6}(#{1,6}|[-*+]|\d{1,4}[.)])\s+/gm, '')
    .replace(/\*\*|__|`/g, '')
    .replace(/(^|[\s(])[*_](?=\S)/g, '$1')
    .replace(/(\S)[*_](?=[\s.,;:!?)]|$)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
  if (clean.length <= budget) return clean;
  const sentences = clean.split(/(?<=[.!?:\u0964])\s+/);
  let tail = '';
  for (let i = sentences.length - 1; i >= 0; i--) {
    const longer = tail ? `${sentences[i]} ${tail}` : sentences[i];
    if (tail && longer.length > budget) break;
    tail = longer;
  }
  if (tail.length > budget) { // one very long sentence: keep its end, from a whole word
    const cut = tail.slice(tail.length - budget);
    tail = cut.slice(cut.indexOf(' ') + 1);
  }
  return `… ${tail}`;
}

// Calls back with true when the browser has the microphone blocked for this page.
export async function watchMicPermission(onChange) {
  try {
    const status = await navigator.permissions.query({ name: 'microphone' });
    onChange(status.state === 'denied');
    status.onchange = () => onChange(status.state === 'denied');
  } catch {
    // Browser can't tell us in advance; we find out when Talk is pressed.
  }
}

export const MIC_BLOCKED_HELP =
  'The microphone is blocked for this page. Open the site settings (the icon at the left of the address bar, or the ⋮ menu in the app window), set Microphone to Allow, then press Talk again. Typing still works.';
