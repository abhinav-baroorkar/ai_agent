// Bots screen: the bot list in the rail, a tab per open chat.
import { api } from './api.js';
import { $, h, icon, clear } from './dom.js';
import { ChatView } from './chat.js';
import { openBotForm } from './bot-form.js';
import { cleanBot, cleanConnector, cleanModel, initial } from './clean.js';

export function initBots(health) {
  const el = { items: $('#bot-items'), tabs: $('#tabs'), chats: $('#chats'), empty: $('#bots-empty'), add: $('#new-bot') };
  let bots = [];
  const open = new Map(); // bot id -> ChatView
  let active = null;
  const listeners = new Set();
  // The server caps conversations (health.limits.max_sockets); Home keeps one of them.
  const MAX_OPEN = health.max_chats;
  let note = '';

  async function load() {
    try {
      bots = ((await api('GET', '/api/bots')).bots || []).map(cleanBot);
    } catch (err) {
      clear(el.items).append(h('li', { class: 'form-error' }, err.message));
      return;
    }
    for (const [id, chat] of open) {
      const bot = bots.find((b) => b.id === id);
      if (bot) chat.update(bot); else closeChat(id);
    }
    render();
    listeners.forEach((fn) => fn(bots));
  }

  function render() {
    clear(el.items).append(...bots.map((bot) => h('li', {},
      h('button', { class: 'bot-item', type: 'button', 'aria-current': String(bot.id === active), onclick: () => show(bot.id) },
        h('span', { class: 'avatar', 'aria-hidden': 'true' }, initial(bot.name)),
        h('span', { class: 'name' }, bot.name),
        h('span', { class: 'model' }, bot.model),
        open.has(bot.id) && h('span', { class: 'open-dot', title: 'Chat is open' })))));

    clear(el.tabs).append(...[...open.values()].map((chat) => h('div', { class: 'tab', dataset: { active: String(chat.bot.id === active) } },
      h('button', { class: 'tab-open', type: 'button', role: 'tab', 'aria-selected': String(chat.bot.id === active), onclick: () => show(chat.bot.id) },
        chat.busy && h('span', { class: 'busy', title: 'Answering' }), chat.bot.name),
      h('button', { class: 'tab-close', type: 'button', 'aria-label': `Close chat with ${chat.bot.name}`, onclick: () => closeChat(chat.bot.id) }, icon('x')))));

    if (note) el.tabs.append(h('p', { class: 'tab-note', role: 'status' }, note));
    for (const chat of open.values()) chat.el.hidden = chat.bot.id !== active;
    el.empty.hidden = open.size > 0;
    el.chats.hidden = open.size === 0;
  }

  function show(id) {
    const bot = bots.find((b) => b.id === id);
    if (!bot) return;
    note = '';
    if (!open.has(id) && open.size >= MAX_OPEN) {
      note = `${MAX_OPEN} chats can be open at once. Close one to open ${bot.name}.`;
      render();
      return;
    }
    if (!open.has(id)) {
      const chat = new ChatView(bot, { onBusy: render, onEdit: edit });
      open.set(id, chat);
      el.chats.append(chat.el);
    }
    active = id;
    render();
    open.get(id).input.focus();
  }

  function closeChat(id) {
    const chat = open.get(id);
    if (!chat) return;
    note = '';
    chat.close();
    open.delete(id);
    if (active === id) active = open.size ? [...open.keys()].pop() : null;
    render();
  }

  async function lists() {
    const [m, c] = await Promise.allSettled([api('GET', '/api/models'), api('GET', '/api/connectors')]);
    return {
      models: m.status === 'fulfilled' ? (m.value.models || []).map(cleanModel).filter((x) => x.name) : [],
      connectors: c.status === 'fulfilled' ? (c.value.connectors || []).map(cleanConnector) : [],
    };
  }

  async function edit(bot) {
    const main = bots.find((b) => b.builtin);
    const result = await openBotForm({ bot, ...(await lists()), preferModel: main && main.model });
    if (!result) return;
    if (result.deleted) closeChat(result.deleted);
    await load();
    if (result.bot && !bot) show(cleanBot(result.bot).id);
  }

  el.add.addEventListener('click', () => edit(null));

  return {
    load,
    edit,
    get bots() { return bots; },
    onChange(fn) { listeners.add(fn); },
  };
}
