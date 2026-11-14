// Bots screen: the bot list in the rail, a tab per open chat.
import { api } from './api.js';
import { $, h, icon, clear } from './dom.js';
import { ChatView } from './chat.js';
import { openBotForm } from './bot-form.js';

export function initBots() {
  const el = { items: $('#bot-items'), tabs: $('#tabs'), chats: $('#chats'), empty: $('#bots-empty'), add: $('#new-bot') };
  let bots = [];
  const open = new Map(); // bot id -> ChatView
  let active = null;
  const listeners = new Set();

  async function load() {
    try {
      bots = (await api('GET', '/api/bots')).bots;
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
        h('span', { class: 'avatar', 'aria-hidden': 'true' }, bot.name.trim().charAt(0).toUpperCase()),
        h('span', { class: 'name' }, bot.name),
        h('span', { class: 'model' }, bot.model),
        open.has(bot.id) && h('span', { class: 'open-dot', title: 'Chat is open' })))));

    clear(el.tabs).append(...[...open.values()].map((chat) => h('div', { class: 'tab', dataset: { active: String(chat.bot.id === active) } },
      h('button', { class: 'tab-open', type: 'button', role: 'tab', 'aria-selected': String(chat.bot.id === active), onclick: () => show(chat.bot.id) },
        chat.busy && h('span', { class: 'busy', title: 'Answering' }), chat.bot.name),
      h('button', { class: 'tab-close', type: 'button', 'aria-label': `Close chat with ${chat.bot.name}`, onclick: () => closeChat(chat.bot.id) }, icon('x')))));

    for (const chat of open.values()) chat.el.hidden = chat.bot.id !== active;
    el.empty.hidden = open.size > 0;
    el.chats.hidden = open.size === 0;
  }

  function show(id) {
    const bot = bots.find((b) => b.id === id);
    if (!bot) return;
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
    chat.close();
    open.delete(id);
    if (active === id) active = open.size ? [...open.keys()].pop() : null;
    render();
  }

  async function lists() {
    const [m, c] = await Promise.allSettled([api('GET', '/api/models'), api('GET', '/api/connectors')]);
    return {
      models: m.status === 'fulfilled' ? m.value.models : [],
      connectors: c.status === 'fulfilled' ? c.value.connectors : [],
    };
  }

  async function edit(bot) {
    const result = await openBotForm({ bot, ...(await lists()) });
    if (!result) return;
    if (result.deleted) closeChat(result.deleted);
    await load();
    if (result.bot && !bot) show(result.bot.id);
  }

  el.add.addEventListener('click', () => edit(null));

  return {
    load,
    edit,
    get bots() { return bots; },
    onChange(fn) { listeners.add(fn); },
  };
}
