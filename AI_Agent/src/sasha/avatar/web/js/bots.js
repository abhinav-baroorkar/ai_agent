// Bots screen: the bot list in the rail, a tab per open chat.
import { api } from './api.js';
import { $, h, icon, clear } from './dom.js';
import { ChatView } from './chat.js';
import { openBotForm } from './bot-form.js';
import { cleanBot, cleanConnector, cleanModel, initial } from './clean.js';
import { initBrowserPanel } from './browser-panel.js';

export function initBots(health) {
  const el = {
    items: $('#bot-items'), tabs: $('#tabs'), chats: $('#chats'), empty: $('#bots-empty'), add: $('#new-bot'),
    body: $('.bots-body'),
  };
  let bots = [];
  const open = new Map(); // bot id -> ChatView
  let active = null;
  const listeners = new Set();
  // The server caps conversations (health.limits.max_sockets); Home keeps one of them.
  const MAX_OPEN = health.max_chats;
  let note = '';
  const panel = initBrowserPanel(health);
  const browserIds = new Set(['browser']); // ids of connectors of kind "browser"
  const panelOpen = new Map(); // bot id -> whether its chat shows the browser panel
  const canBrowse = (bot) => bot.connectors.some((id) => browserIds.has(id));

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
    // which connectors are browsers (normally just the one called "browser")
    api('GET', '/api/connectors').then((r) => {
      const ids = (r.connectors || []).map(cleanConnector).filter((c) => c.kind === 'browser').map((c) => c.id);
      if (ids.length) { browserIds.clear(); ids.forEach((id) => browserIds.add(id)); showPanel(); }
    }).catch(() => {});
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
    el.body.hidden = open.size === 0;
    showPanel();
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
      const chat = new ChatView(bot, { onBusy: render, onEdit: edit, onBrowser: toggleBrowser });
      if (!panelOpen.has(id)) panelOpen.set(id, canBrowse(bot)); // open by default for a bot that browses
      open.set(id, chat);
      el.chats.append(chat.el);
    }
    active = id;
    render();
    open.get(id).input.focus();
  }

  // The panel follows the chat in front: one browser, one viewer, whichever chat is shown.
  function showPanel() {
    const bot = bots.find((b) => b.id === active);
    const isOpen = !!bot && open.has(bot.id) && panelOpen.get(bot.id) === true;
    for (const chat of open.values()) chat.browserBtn.setAttribute('aria-pressed', String(panelOpen.get(chat.bot.id) === true));
    panel.show(bot || null, !!bot && canBrowse(bot), isOpen, () => bot && toggleBrowser(bot));
  }

  function toggleBrowser(bot) {
    panelOpen.set(bot.id, !panelOpen.get(bot.id));
    showPanel();
  }

  function closeChat(id) {
    const chat = open.get(id);
    if (!chat) return;
    note = '';
    chat.close();
    open.delete(id);
    panelOpen.delete(id);
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
