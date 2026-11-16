// Bots: the way in on Home, the "Your bots" page, and the chats.
// One chat is in front at a time; a few can stay open behind it.
import { api } from './api.js';
import { $, h, icon, clear } from './dom.js';
import { ChatView } from './chat.js';
import { cleanBot, cleanConnector } from './clean.js';
import { initBrowserPanel } from './browser-panel.js';
import { miniOrb, wakeOrbs } from './faces/mini-orb.js';
import { colorOf, usesOf, purposeOf, STARTS } from './bot-look.js';
import { initSetup } from './setup.js';

const HOME_ORBS = 3; // how many bots' orbs the way in on Home shows

export function initBots(health) {
  const el = {
    homeBox: $('#home-bots'), homeOrbs: $('#home-bot-orbs'), homeCount: $('#home-bot-count'),
    cards: $('#bot-cards'), note: $('#bots-note'), chats: $('#chats'),
  };
  const name = health.assistant.name;
  const MAIN = health.default_bot; // the main assistant; any other built-in bot is the wake word's
  const MAX_OPEN = health.max_chats; // the server caps conversations; Home keeps one of them
  let bots = [];
  let connectors = [];
  const open = new Map(); // bot id -> ChatView
  let active = null;
  const orbs = new Map(); // "id:size" -> canvas, so an orb is made once per place
  const listeners = new Set();
  const panel = initBrowserPanel(health);

  // A bot with the direct browser, or the main assistant while web lookups are on (v0.5.1):
  // its chat can show the live view while its browser is in use.
  const canBrowse = (bot) => connectors.some((c) => c.kind === 'browser' &&
    (bot.connectors.includes(c.id) || (bot.id === MAIN && c.enabled && c.tools.includes('browse'))));
  const look = (bot) => ({ style: bot.style, color: colorOf(bot), uses: usesOf(bot, connectors), purpose: purposeOf(bot, name, MAIN) });
  function orb(bot, size) {
    const key = `${bot.id}:${size}:${bot.style}`;
    if (!orbs.has(key)) orbs.set(key, miniOrb(colorOf(bot), size, { style: bot.style }));
    return orbs.get(key);
  }

  async function load() {
    const [b, c] = await Promise.allSettled([api('GET', '/api/bots'), api('GET', '/api/connectors')]);
    if (b.status !== 'fulfilled') {
      clear(el.cards).append(h('p', { class: 'form-error' }, b.reason.message));
      return;
    }
    bots = (b.value.bots || []).map(cleanBot);
    if (c.status === 'fulfilled') connectors = (c.value.connectors || []).map(cleanConnector);
    for (const [id, chat] of open) {
      const bot = bots.find((x) => x.id === id);
      if (bot) chat.update(bot, look(bot)); else closeChat(id);
    }
    render();
    listeners.forEach((fn) => fn(bots));
  }

  function render() {
    const mine = bots.filter((b) => !b.builtin);
    // Home: one way in, with the first few bots' orbs and how many there are
    clear(el.homeOrbs).append(...mine.slice(0, HOME_ORBS).map((bot) => orb(bot, 34)));
    el.homeCount.textContent = !mine.length ? 'Make your first helper'
      : `${mine.length} ${mine.length === 1 ? 'helper' : 'helpers'} for single jobs`;
    el.homeBox.hidden = false;

    // The page: a way to make one, the main assistant, then the rest
    const main = bots.filter((b) => b.id === MAIN);
    const voice = bots.filter((b) => b.builtin && b.id !== MAIN);
    clear(el.cards).append(newCard(), ...[...main, ...voice, ...mine].map(card));
    wakeOrbs(); // cached orbs were just put back on the page

    for (const chat of open.values()) chat.el.hidden = chat.bot.id !== active;
    const front = bots.find((b) => b.id === active);
    panel.show(front && open.has(front.id) ? front : null, !!front && canBrowse(front));
  }

  function newCard() {
    return h('article', { class: 'bot-card new' },
      h('div', { class: 'row' }, h('span', { class: 'plus' }, icon('plus')), h('h3', {}, 'Make a new bot')),
      h('div', { class: 'starts' }, ...STARTS.map((s) => h('button', { type: 'button', onclick: () => setup.begin({ start: s.id }) }, s.title))));
  }

  function card(bot) {
    const lk = look(bot);
    const isMain = bot.id === MAIN;
    const isVoice = bot.builtin && !isMain; // the wake word's bot: changeable, not a chat
    const uses = isMain ? `Main assistant · ${lk.uses.text.toLowerCase()}` : isVoice ? `Wake word · ${lk.uses.text.toLowerCase()}` : lk.uses.text;
    return h('article', { class: `bot-card ${isMain ? 'main' : isVoice ? 'voice' : ''}` },
      h('div', { class: 'row' }, orb(bot, 52), h('div', { style: 'min-width:0' }, h('h3', {}, bot.name),
        h('span', { class: 'uses', dataset: { kind: lk.uses.kind } }, uses))),
      h('p', {}, lk.purpose),
      h('div', { class: 'foot' },
        !isVoice && h('button', { class: 'btn primary', type: 'button', onclick: () => go(bot.id) }, isMain ? 'Chat in text' : 'Chat'),
        h('button', { class: isVoice ? 'btn' : 'btn quiet', type: 'button', onclick: () => setup.begin({ bot }) }, 'Change'),
        open.has(bot.id) && h('span', { class: 'open-mark' }, 'Chat open')));
  }

  // Bring a bot's chat to the front, opening it if there is room.
  function go(id) {
    const bot = bots.find((b) => b.id === id);
    if (!bot) return;
    el.note.hidden = true;
    if (!open.has(id) && open.size >= MAX_OPEN) return atLimit(bot);
    if (!open.has(id)) {
      const chat = new ChatView(bot, look(bot), { health, onEdit: (b) => setup.begin({ bot: b }), onClose: (b) => { closeChat(b.id); location.hash = '#bots'; } });
      open.set(id, chat);
      el.chats.append(chat.el);
    }
    active = id;
    location.hash = '#chat';
    render();
    open.get(id).input.focus();
    return undefined;
  }

  // Only so many conversations can be open at once. Say so, and offer the way out.
  function atLimit(bot) {
    const many = MAX_OPEN === 1 ? 'one chat' : `${MAX_OPEN} chats`;
    clear(el.note).append(
      h('span', {}, `You already have ${many} open, and that is as many as can be open at once. Close one to open ${bot.name}:`),
      ...[...open.values()].map((c) => h('button', { class: 'btn small', type: 'button', onclick: () => { closeChat(c.bot.id); go(bot.id); } }, `Close ${c.bot.name}`)),
      h('button', { class: 'btn small quiet', type: 'button', onclick: () => { el.note.hidden = true; } }, 'Not now'));
    el.note.hidden = false;
    location.hash = '#bots';
  }

  function closeChat(id) {
    const chat = open.get(id);
    if (!chat) return;
    chat.close();
    open.delete(id);
    if (active === id) active = null;
    render();
  }

  const setup = initSetup({
    health,
    lists: () => ({ bots, connectors, mainId: MAIN, styles: bots.some((b) => b.hasStyle) }),
    async saved(bot, isNew) {
      await load();
      if (isNew) go(cleanBot(bot).id); else location.hash = open.has(bot.id) && active === bot.id ? '#chat' : '#bots';
    },
    async deleted(id) {
      closeChat(id);
      await load();
      location.hash = '#bots';
    },
  });

  return {
    load,
    get bots() { return bots; },
    get hasChat() { return active !== null && open.has(active); },
    change(bot) { setup.begin({ bot }); },
    // opened straight at #setup, the lists may not have arrived yet
    showSetup() { if (bots.length) setup.show(); else load().then(() => setup.show()); },
    leaveSetup() { setup.leave(); },
    onChange(fn) { listeners.add(fn); },
  };
}
