// One open chat with one bot: transcript, tool chips, composer.
import { h, icon, setText } from './dom.js';
import { Conversation } from './socket.js';
import { initial } from './clean.js';
import { renderMarkdown } from './md.js';

function chip(tool) {
  return h('span', { class: `chip ${tool.ok ? 'ok' : 'bad'}`, title: tool.ok ? 'Tool ran' : 'Tool failed' },
    icon(tool.ok ? 'check' : 'x'), String(tool.name ?? 'tool'));
}

export class ChatView {
  constructor(bot, { onBusy, onEdit, onBrowser }) {
    this.bot = bot;
    this.onBusy = onBusy;
    this.busy = false;
    this.pending = null; // the assistant message being streamed
    this.conv = new Conversation(bot.id);

    this.title = h('span', { class: 'title' });
    this.meta = h('span', { class: 'meta' });
    this.avatar = h('span', { class: 'avatar', 'aria-hidden': 'true' });
    this.offline = h('span', { class: 'pill', dataset: { tone: 'offline' }, hidden: true }, 'Offline');
    this.log = h('div', { class: 'log', role: 'log', 'aria-live': 'polite', tabIndex: 0 });
    this.input = h('textarea', { rows: 1, 'aria-label': 'Message', maxLength: 8000,
      onkeydown: (ev) => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); this.submit(); } },
      oninput: () => this.grow() });
    this.sendBtn = h('button', { class: 'btn primary', type: 'submit' }, 'Send');
    this.stopBtn = h('button', { class: 'btn danger', type: 'button', hidden: true,
      onclick: () => this.conv.cancel() }, icon('stop'), 'Stop');
    this.refused = false;
    // shows or hides the live view of the assistant's browser beside this chat
    this.browserBtn = h('button', { class: 'btn small quiet', type: 'button', 'aria-pressed': 'false',
      onclick: () => onBrowser(this.bot) }, 'Browser');

    this.el = h('section', { class: 'chat', role: 'tabpanel' },
      h('header', { class: 'chat-head' }, this.avatar,
        h('div', { class: 'who' }, this.title, this.meta),
        h('span', { class: 'grow' }), this.offline,
        this.browserBtn,
        h('button', { class: 'btn small quiet', type: 'button', onclick: () => this.restart() }, 'Clear chat'),
        h('button', { class: 'btn small', type: 'button', onclick: () => onEdit(this.bot) }, 'Edit bot')),
      this.log,
      h('form', { class: 'composer', onsubmit: (ev) => { ev.preventDefault(); this.submit(); } },
        h('div', { class: 'field' }, this.input, this.stopBtn, this.sendBtn),
        h('p', { class: 'hint' }, 'Enter sends. Shift+Enter starts a new line.')));

    this.update(bot);
    this.showEmpty();
    this.conv
      .on('online', () => this.setOnline(true))
      .on('offline', () => this.setOnline(false))
      .on('refused', (m) => {
        this.refused = true;
        this.offline.hidden = true;
        this.add(h('div', { class: 'msg error', role: 'alert' }, m.message));
      })
      .on('state', (m) => this.setBusy(m.state !== 'idle'))
      .on('tool', (m) => this.reply().chips.append(chip(m)))
      .on('reply_delta', (m) => { const r = this.reply(); r.text += String(m.text ?? ''); this.paintSoon(r); })
      .on('reply', (m) => this.finish(m))
      .on('error', (m) => { this.add(h('div', { class: 'msg error', role: 'alert' }, String(m.message ?? 'Something went wrong.'))); this.pending = null; });
    this.setOnline(false);
  }

  update(bot) {
    this.bot = bot;
    this.title.textContent = bot.name;
    this.avatar.textContent = initial(bot.name);
    const n = bot.connectors.length;
    this.meta.textContent = `${bot.model} · ${n === 0 ? 'no connectors' : n === 1 ? '1 connector' : `${n} connectors`}`;
    this.input.placeholder = `Message ${bot.name}`;
    if (this.log && this.log.querySelector('.log-empty')) this.showEmpty();
  }

  setOnline(on) {
    this.offline.hidden = on;
    this.sendBtn.disabled = !on;
    if (!on) this.setBusy(false);
  }

  setBusy(busy) {
    if (busy && !this.pending && !this.done) this.reply();
    if (!busy) this.done = false;
    if (!busy && this.pending) {
      // Stopped or dropped before a final reply: keep what arrived.
      if (!this.pending.text && !this.pending.chips.childElementCount) this.pending.el.remove();
      else {
        this.pending.dots.replaceWith(h('span', { class: 'timing' }, 'Stopped'));
        this.paint(this.pending); // whatever arrived, drawn in full
      }
      this.pending = null;
    }
    this.busy = busy;
    this.stopBtn.hidden = !busy;
    this.onBusy(this);
  }

  showEmpty() {
    const about = this.bot.system_prompt.trim();
    this.log.replaceChildren(h('div', { class: 'log-empty' },
      h('span', { class: 'avatar big', 'aria-hidden': 'true' }, initial(this.bot.name)),
      h('strong', {}, this.bot.name),
      h('p', {}, about ? (about.length > 160 ? `${about.slice(0, 157)}…` : about) : 'No instructions yet. Edit the bot to give it some.'),
      h('p', { class: 'hint' }, `Runs on ${this.bot.model}, on this computer.`)));
  }

  add(node) {
    const empty = this.log.querySelector('.log-empty');
    if (empty) empty.remove();
    this.log.append(node);
    this.log.scrollTop = this.log.scrollHeight;
    return node;
  }

  // The assistant message currently being built (created on first use).
  reply() {
    if (this.pending) return this.pending;
    const body = h('div', { class: 'body' });
    const chips = h('div', { class: 'chips' });
    const dots = h('span', { class: 'typing', 'aria-label': 'Thinking' }, h('i'), h('i'), h('i'));
    const col = h('div', { class: 'col' }, h('span', { class: 'from' }, this.bot.name), chips, body, dots);
    const el = h('div', { class: 'msg bot' }, h('span', { class: 'avatar', 'aria-hidden': 'true' }, initial(this.bot.name)), col);
    this.pending = { el, col, body, chips, dots, text: '', frame: 0 };
    this.add(el);
    return this.pending;
  }

  // While a reply streams, draw it at most once per animation frame, with the latest text.
  paintSoon(r) {
    if (r.frame) return;
    r.frame = requestAnimationFrame(() => {
      r.frame = 0;
      this.paint(r);
    });
  }

  // The bot's text gets its simple formatting (md.js builds nodes; nothing is parsed as HTML).
  paint(r) {
    cancelAnimationFrame(r.frame);
    r.frame = 0;
    r.body.replaceChildren(renderMarkdown(r.text));
    this.log.scrollTop = this.log.scrollHeight;
  }

  finish(m) {
    const r = this.reply();
    r.text = String(m.text ?? '');
    if (Array.isArray(m.tool_calls)) r.chips.replaceChildren(...m.tool_calls.map(chip));
    r.dots.remove();
    if (m.timing && m.timing.total_ms != null) {
      const speed = m.timing.tokens_per_s ? ` · ${Math.round(m.timing.tokens_per_s)} tokens/s` : '';
      r.col.append(h('span', { class: 'timing' }, `${(m.timing.total_ms / 1000).toFixed(1)} s${speed}`));
    }
    this.paint(r);
    this.pending = null;
    this.done = true; // a "speaking" state may still follow; it must not open a new bubble
  }

  submit() {
    const text = this.input.value.trim();
    if (!text) return;
    if (!this.conv.online) return;
    // If an answer is still running it is stopped first; the message goes out once the server is idle.
    this.conv.whenIdle(() => {
      const body = h('div', { class: 'body' });
      setText(body, text);
      this.add(h('div', { class: 'msg user' }, body));
      this.conv.sendText(text);
    });
    this.input.value = '';
    this.grow();
  }

  restart() {
    this.conv.reset();
    this.conv.waiting = null;
    this.pending = null;
    this.showEmpty();
    this.input.focus();
  }

  grow() {
    this.input.style.height = 'auto';
    this.input.style.height = `${Math.min(this.input.scrollHeight + 2, 144)}px`;
  }

  close() {
    this.conv.close();
    this.el.remove();
  }
}
