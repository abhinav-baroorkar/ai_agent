// The live view of the assistant's own browser, beside a bot's chat: watch and
// stop, nothing more. Everything that comes from a web page is untrusted: the
// picture is shown only as a JPEG in an <img>, and the site and title only as
// plain text. Nothing from it goes into the page title, the address, the
// console or any storage.
import { api, HAS_TOKEN, MOCK } from './api.js';
import { $, h, clear } from './dom.js';
import { cleanBrowserState } from './clean.js';
import { BrowserLink } from './browser-link.js';
import { initTakeover } from './browser-takeover.js';

const MAX_JPEG_CHARS = 720000; // a little over the server's own 512 KB limit, as base64
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
const JPEG_START = '/9j/'; // every JPEG begins FF D8 FF, which is this in base64
const MAX_SIDE = 10000; // frame width and height are used for layout only if they are sane
const STOPPED = {
  idle: 'The browser closed itself after sitting idle.',
  owner: 'You stopped the browser.',
  brain_stopping: 'The browser closed because the assistant is shutting down.',
  crashed: 'The browser crashed. It starts again the next time it is needed.',
};
const STOP_NOTE_MS = 8000;
const WAITING = 'The browser isn’t running. It starts when you ask the assistant to look something up.';

export function initBrowserPanel(health) {
  const el = {
    panel: $('#browser-panel'), title: $('#browser-title'), agent: $('#browser-agent'), stop: $('#browser-stop'),
    address: $('#browser-address'), site: $('#browser-site'), page: $('#browser-page'),
    pageLine: $('#browser-pageline'),
    img: $('#browser-img'), msg: $('#browser-msg'), again: $('#browser-again'), tabs: $('#browser-tabs'),
    guard: $('#browser-guard'), mock: $('#mock-browser'), body: $('.bots-body'),
  };
  const link = new BrowserLink();
  const takeover = initTakeover($('#browser-takeover'), health);
  let bot = null;
  let hasConnector = false;
  let open = false;
  let state = cleanBrowserState(null);
  let lastStop = '';
  let stopNoteUntil = 0; // how long the reason for a stop stays on screen
  let shown = false;
  let failed = '';
  // The owner's own choice: the panel stays open (Browser button, Take over in a chat) or
  // is put away. Without either, it shows itself only while the browser is in use.
  let pinned = false;
  let putAway = false;
  const listeners = new Set();
  el.title.textContent = `${health.assistant.name}’s browser`;

  const visible = () => open && shown && !$('#chat').hidden && !document.hidden;

  // Connect only for a panel that is open on a bot that can browse; ask for frames
  // only while it can actually be seen. Nobody watching means nothing is captured.
  function sync() {
    if (open && hasConnector) link.open(); else link.close();
    render(); // decides whether the panel is shown
    link.watch(visible() && hasConnector);
  }

  function message() {
    if (!hasConnector) {
      return `${bot ? bot.name : 'This bot'} can’t use the web.`;
    }
    if (link.status === 'busy') return 'Another window is already watching the browser. Only one can watch at a time.';
    if (link.status === 'refused') {
      return HAS_TOKEN
        ? 'The assistant refused this window’s request to watch the browser.'
        : 'Open the window with “sasha window” (or reopen it) to watch the browser.';
    }
    if (link.status === 'offline') return 'Can’t reach the assistant. Trying again…';
    if (link.status !== 'open') return 'Connecting…';
    if (!state.enabled) return 'The browser is off. Turn it on in Settings.';
    if (state.mode === 'owner') return 'The browser is in its own window on your screen. Nothing of it is shown here.';
    if (state.mode === 'switching') return 'Opening the browser in its own window…';
    if (state.mode === 'restoring') return 'Cleaning up after your window…';
    if (!state.running && state.person_check) return 'The search site wants to check you’re a person. Press Take over above to solve it in your own window.';
    if (!state.running) return [lastStop, WAITING].filter(Boolean).join(' ');
    return el.img.hidden ? 'Waiting for the first picture…' : '';
  }

  function render() {
    // The panel is there only while this bot's browser is: running, just stopped, or
    // when the owner needs telling why it can't be watched.
    if (link.status === 'open' && state.mode && state.mode !== 'assistant') putAway = false; // his window: always said
    const auto = showsByItself(link.status, state, performance.now() < stopNoteUntil);
    const wanted = open && hasConnector && !putAway && (pinned || auto);
    shown = wanted;
    for (const fn of listeners) fn({ botId: bot && bot.id, canBrowse: open && hasConnector, shown });
    el.panel.hidden = !wanted;
    el.body.classList.toggle('with-browser', wanted);
    if (!wanted) return dropPicture();
    takeover.show(link.status === 'open' ? state : null);
    const live = hasConnector && link.status === 'open' && state.running && state.mode !== 'owner' && state.mode !== 'restoring';
    if (!live) dropPicture();
    el.address.hidden = !live;
    el.stop.disabled = !live;
    el.agent.hidden = !(hasConnector && link.status === 'open');
    el.agent.dataset.tone = state.mode === 'restoring' ? 'thinking' : !state.running ? 'off' : state.agent === 'working' ? 'thinking' : 'ok';
    const inUse = !state.user ? 'In use now' : state.via === 'helper' ? `${state.user} is looking something up` : `In use by ${state.user}`;
    el.agent.textContent = state.mode === 'owner' ? 'You’re driving' : state.mode === 'restoring' ? 'Cleaning up' : !state.running ? 'Not running' : state.agent === 'working' ? inUse : 'Open, idle';
    const text = failed || message();
    el.msg.textContent = text;
    el.msg.hidden = !text;
    el.again.hidden = !(hasConnector && (link.status === 'busy' || link.status === 'refused'));

    const tab = state.tabs.find((t) => t.current) || state.tabs[0];
    el.site.textContent = live && tab ? tab.site || '(no site)' : '';
    el.page.textContent = live && tab ? tab.title : '';
    el.pageLine.hidden = !(live && tab && tab.title);
    clear(el.tabs).append(...(live ? state.tabs : []).map((t) => h('li', { class: 'browser-tab', 'aria-current': t.current ? 'true' : null },
      h('span', { class: 'n' }, String(t.tab)), h('bdi', { class: 'site', dir: 'ltr' }, t.site || '(no site)'),
      h('bdi', { class: 'title', dir: 'auto' }, t.title))));
    el.tabs.hidden = !live || state.tabs.length < 2;
    el.guard.textContent = live ? guardLine(state.guard) : '';
    el.guard.hidden = !live;
  }

  function dropPicture() {
    el.img.hidden = true;
    el.img.removeAttribute('src'); // nothing stale stays on screen
  }

  async function onFrame(m) {
    const seq = m.seq;
    // no picture while the owner drives: his window is his, even if a frame arrives late
    const ok = visible() && state.running && state.mode !== 'owner' && state.mode !== 'restoring' && typeof m.jpeg === 'string' && m.jpeg.length <= MAX_JPEG_CHARS &&
      m.jpeg.startsWith(JPEG_START) && BASE64.test(m.jpeg);
    if (ok) {
      const w = Number(m.w);
      const hgt = Number(m.h);
      const sane = (n) => Number.isFinite(n) && n > 0 && n <= MAX_SIDE;
      if (sane(w) && sane(hgt)) el.img.style.aspectRatio = `${w} / ${hgt}`;
      el.img.src = `data:image/jpeg;base64,${m.jpeg}`; // only ever a JPEG in an <img>
      try {
        await el.img.decode();
        if (visible() && state.running) {
          el.img.hidden = false;
          el.msg.hidden = true;
        }
      } catch {
        dropPicture(); // not a picture after all
      }
    }
    link.ack(seq); // always, so the stream never stalls on a frame we skipped
  }

  link
    .on('status', () => { if (link.status !== 'open') state = cleanBrowserState(null); render(); })
    .on('state', (m) => { state = cleanBrowserState(m); if (state.running) lastStop = ''; failed = ''; sync(); })
    .on('frame', onFrame)
    .on('stopped', (m) => {
      lastStop = STOPPED[m.reason] || 'The browser stopped.';
      stopNoteUntil = performance.now() + STOP_NOTE_MS;
      setTimeout(sync, STOP_NOTE_MS + 50);
      state = { ...state, running: false, agent: 'idle', user: '', tabs: [] }; // a late frame must not be drawn
      dropPicture();
      render();
    })
    .on('error', (m) => { failed = String(m.message ?? ''); render(); });

  el.stop.addEventListener('click', async () => {
    failed = '';
    if (link.stop()) return;
    try {
      await api('POST', '/api/browser/stop');
    } catch (err) {
      failed = err.message;
      render();
    }
  });
  el.again.addEventListener('click', () => { failed = ''; link.again(); });
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('hashchange', () => setTimeout(sync, 0)); // after the screens have switched
  window.addEventListener('pagehide', () => link.close());

  if (MOCK) {
    el.mock.hidden = false;
    el.mock.addEventListener('change', async () => {
      const mock = await import('./mock-browser.js');
      if (el.mock.value === 'use') mock.use(); else if (el.mock.value) mock.stop(el.mock.value);
      el.mock.value = '';
    });
  }

  return {
    // Which chat is in front, whether its bot may browse, and whether its panel is open.
    // The chat in front, and whether its bot may use the web.
    show(nextBot, canBrowse) {
      const changedBot = (nextBot && nextBot.id) !== (bot && bot.id);
      bot = nextBot;
      hasConnector = !!canBrowse;
      open = !!bot && hasConnector;
      if (changedBot) { lastStop = ''; failed = ''; stopNoteUntil = 0; pinned = false; putAway = false; }
      sync();
    },
    // The chat's Browser button: show the panel, or put it away.
    toggle() {
      if (shown) { pinned = false; putAway = true; } else { pinned = true; putAway = false; }
      sync();
      if (shown) el.panel.focus();
    },
    // A failed lookup's Take over: open the panel and press its Take over.
    takeOver() {
      pinned = true;
      putAway = false;
      sync();
      el.panel.focus();
      takeover.start();
    },
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
}

// Whether the panel shows itself (without the owner's Browser button): while the browser
// runs, during a take-over, just after a stop, when watching was refused or busy, and while
// a search's person check waits for him (person_check), so the server's line "Take over in
// the browser panel beside this chat" stays true after the browser has stopped.
export function showsByItself(linkStatus, state, justStopped) {
  if (linkStatus === 'busy' || linkStatus === 'refused') return true;
  if (linkStatus !== 'open') return false;
  const takingOver = !!state.mode && state.mode !== 'assistant';
  return state.running || takingOver || state.person_check || justStopped;
}

// "3 addresses refused" and friends: the safety net, in one quiet line.
function guardLine(guard) {
  const refused = Object.values(guard.refused).reduce((a, b) => a + b, 0);
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const parts = [`${plural(refused, 'address', 'addresses')} refused`];
  if (guard.background_refused) parts.push(`${plural(guard.background_refused, 'background request', 'background requests')} refused`);
  parts.push(`${guard.allowed} allowed`);
  return parts.join(' · ');
}
