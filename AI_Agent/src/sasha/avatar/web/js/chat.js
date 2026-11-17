// One open chat with one bot: transcript, tool chips, composer.
import { h, icon, setText } from './dom.js';
import { Conversation } from './socket.js';
import { initial } from './clean.js';
import { renderMarkdown } from './md.js';
import { lookedUp } from './clean.js';
import { VoiceInput, voiceSupported } from './voice-input.js';
import { cssColor } from './bot-look.js';
import { miniOrb, wakeOrbs, setOrb } from './faces/mini-orb.js';

const HINT = 'Enter sends. Shift+Enter starts a new line.';
// The server's person-check line says this (browser/pages.py PERSON_CHECK; a test keeps them in step).
const PERSON_CHECK = /wants to check you[’']re a person/i;
// a refusal that means this server will never take voice on a chat: stop offering it
const VOICE_REFUSED = /not available|not allowed|not supported|unsupported/i;

function chip(tool) {
  return h('span', { class: `chip ${tool.ok ? 'ok' : 'bad'}`, title: tool.ok ? 'Tool ran' : 'Tool failed' },
    icon(tool.ok ? 'check' : 'x'), String(tool.name ?? 'tool'));
}

export class ChatView {
  // look: { color: [r,g,b], uses: {text, kind}, purpose } (how this bot is shown; see bot-look.js)
  constructor(bot, look, { health, onEdit, onClose, browser }) {
    this.browser = browser; // the browser panel beside the chat, for a bot that may use the web
    this.bot = bot;
    this.busy = false;
    this.pending = null; // the assistant message being streamed
    this.conv = new Conversation(bot.id);

    this.title = h('span', { class: 'title' });
    this.purpose = h('span', { class: 'purpose' });
    this.usesTag = h('span', { class: 'uses' });
    this.orb = h('span', { class: 'orb-slot' });
    this.offline = h('span', { class: 'pill', dataset: { tone: 'offline' }, hidden: true }, 'Offline');
    this.log = h('div', { class: 'log', role: 'log', 'aria-live': 'polite', tabIndex: 0 });
    this.input = h('textarea', { rows: 1, 'aria-label': 'Message', maxLength: 8000,
      onkeydown: (ev) => { if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); this.submit(); } },
      oninput: () => this.grow() });
    this.sendBtn = h('button', { class: 'btn primary', type: 'submit' }, 'Send');
    this.stopBtn = h('button', { class: 'btn danger', type: 'button', hidden: true,
      onclick: () => this.conv.cancel() }, icon('stop'), 'Stop');
    this.hint = h('p', { class: 'hint' }, HINT);
    // Always in the header of a chat whose bot may use the web: the way to its browser.
    this.browserLabel = h('span', {}, 'Show browser');
    this.browserBtn = h('button', { class: 'btn small browser-toggle', type: 'button', hidden: true,
      'aria-controls': 'browser-panel', 'aria-pressed': 'false', onclick: () => browser && browser.toggle() }, icon('globe'), this.browserLabel);
    this.lookupFailed = false; // a web tool failed in the reply being built
    if (browser) {
      browser.onChange(({ botId, canBrowse, shown }) => {
        if (botId !== this.bot.id) return;
        this.browserBtn.hidden = !canBrowse;
        this.browserBtn.setAttribute('aria-pressed', String(shown));
        this.browserLabel.textContent = shown ? 'Hide browser' : 'Show browser';
      });
    }
    this.refused = false;
    // Speak a message instead of typing it; the reply still comes as text.
    this.micBtn = h('button', { class: 'btn mic-chat', type: 'button', 'aria-pressed': 'false', 'aria-label': 'Speak a message',
      hidden: !voiceSupported(health) || ChatView.voiceOff, onclick: () => (this.voice.on ? this.voice.send() : this.voice.start()) }, icon('mic'));
    this.voice = new VoiceInput({
      conv: this.conv, owner: `chat:${bot.id}`,
      isVisible: () => !this.el.hidden && location.hash === '#chat',
      onChange: (v) => {
        if (v.recording) this.showState('listening');
        else if (this.orbState === 'listening') this.showState('idle');
        this.micBtn.setAttribute('aria-pressed', String(v.on));
        this.micBtn.dataset.phase = v.phase;
        this.hint.textContent = v.starting ? 'Starting the microphone…' : v.phase === 'finishing' ? 'Finishing…'
          : v.recording ? 'Listening. It sends when you stop speaking; click the microphone to send now.' : HINT;
      },
      onNotice: (text) => this.add(h('div', { class: 'msg error', role: 'status' }, text)),
    });

    this.el = h('section', { class: 'chat' },
      h('header', { class: 'chat-head' }, this.orb,
        h('div', { class: 'who' }, h('span', { class: 'title' }, this.title, this.usesTag), this.purpose),
        h('span', { class: 'grow' }), this.offline, this.browserBtn,
        h('button', { class: 'btn small quiet', type: 'button', onclick: () => onEdit(this.bot) }, 'Change'),
        h('button', { class: 'btn small quiet', type: 'button', onclick: () => this.restart() }, 'Clear chat'),
        h('button', { class: 'btn small quiet', type: 'button', onclick: () => onClose(this.bot) }, 'Close chat')),
      this.log,
      h('form', { class: 'composer', onsubmit: (ev) => { ev.preventDefault(); this.submit(); } },
        h('div', { class: 'field' }, this.input, this.micBtn, this.stopBtn, this.sendBtn),
        this.hint));

    this.update(bot, look);
    this.showEmpty();
    this.conv
      .on('online', () => this.setOnline(true))
      .on('offline', () => this.setOnline(false))
      .on('refused', (m) => {
        this.refused = true;
        this.offline.hidden = true;
        this.add(h('div', { class: 'msg error', role: 'alert' }, m.message));
      })
      .on('state', (m) => {
        this.setBusy(m.state !== 'idle' && m.state !== 'listening');
        this.showState(m.state === 'speaking' || m.state === 'thinking' ? m.state : this.voice.recording ? 'listening' : m.state);
      })
      .on('reply_delta', () => { this.showState('speaking'); this.lookupNote(false); }) // the reply is arriving: it is talking
      .on('transcript', (m) => { if (m.final && String(m.text ?? '').trim()) this.addMine(String(m.text)); })
      .on('tool', (m) => {
        this.reply().chips.append(chip(m));
        this.lookupNote(m.name === 'browse');
        if (!m.ok && this.browser && this.browser.isWebTool(String(m.name ?? ''))) this.lookupFailed = true;
      })
      .on('reply_delta', (m) => { const r = this.reply(); r.text += String(m.text ?? ''); this.paintSoon(r); })
      .on('reply', (m) => this.finish(m))
      .on('forgot', (m) => this.forgetSince(Number(m.since_s)))
      .on('error', (m) => {
        const text = String(m.message ?? 'Something went wrong.');
        if (this.voice.on) {
          this.voice.cancel();
          if (VOICE_REFUSED.test(text)) { // hide the button everywhere rather than offer one that fails
            ChatView.voiceOff = true;
            document.querySelectorAll('.mic-chat').forEach((b) => { b.hidden = true; });
          }
        }
        this.add(h('div', { class: 'msg error', role: 'alert' }, text));
        this.pending = null;
      });
    this.setOnline(false);
  }

  update(bot, look = this.look) {
    this.bot = bot;
    this.look = look;
    this.title.textContent = bot.name;
    this.purpose.textContent = look.purpose;
    this.usesTag.textContent = look.uses.text;
    this.usesTag.dataset.kind = look.uses.kind;
    this.el.style.setProperty('--hue', cssColor(look.color));
    if (this.orbKey !== `${look.color}:${look.style}`) {
      this.orbKey = `${look.color}:${look.style}`;
      this.orb.replaceChildren(miniOrb(look.color, 44, { style: look.style, state: this.orbState || 'idle' }));
    }
    this.input.placeholder = `Message ${bot.name}`;
    wakeOrbs();
    if (this.log.querySelector('.log-empty')) this.showEmpty();
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
  }

  showEmpty() {
    this.log.replaceChildren(h('div', { class: 'log-empty' },
      miniOrb(this.look.color, 84, { style: this.look.style }),
      h('strong', {}, this.bot.name),
      h('p', {}, this.look.purpose),
      h('p', { class: 'hint' }, `${this.look.uses.text}. Runs on ${this.bot.model}, on this computer.`)));
  }

  add(node) {
    const empty = this.log.querySelector('.log-empty');
    if (empty) empty.remove();
    node.dataset.at ||= String(Date.now());
    this.log.append(node);
    this.log.scrollTop = this.log.scrollHeight;
    return node;
  }

  // Memory's `forgot` frame: the server dropped this chat's turns from the last
  // `since_s` seconds; drop them from the screen too (a reply in progress included).
  forgetSince(sinceS) {
    if (!(sinceS > 0)) return;
    const cutoff = Date.now() - sinceS * 1000;
    for (const m of this.log.querySelectorAll('.msg')) if (Number(m.dataset.at) >= cutoff) m.remove();
    if (this.pending && !this.pending.el.isConnected) this.pending = null;
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

  // Right under a failed lookup: the same Take over as the browser panel's, so it never has to be found.
  takeOverOffer(personCheck) {
    const text = personCheck
      ? 'The search site wants to check you’re a person. Take over the browser, solve the check yourself, hand it back, then ask again.'
      : 'The web lookup didn’t work. If the site wants to check you’re a person, take over the browser and solve it yourself, then ask again.';
    return h('div', { class: 'takeover-offer', role: 'group', 'aria-label': 'The web lookup didn’t work' },
      h('p', {}, text),
      h('button', { class: 'btn small primary', type: 'button', 'aria-controls': 'browser-panel', onclick: () => this.browser.takeOver() },
        icon('globe'), 'Take over the browser'));
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
    this.lookupNote(false);
    const sites = lookedUp(m.sites);
    if (sites) r.col.append(h('span', { class: 'looked-up' }, `looked up: ${sites}`)); // text only, never links
    if (this.lookupFailed) r.col.append(this.takeOverOffer(PERSON_CHECK.test(r.text)));
    this.lookupFailed = false;
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
      this.addMine(text);
      this.conv.sendText(text);
    });
    this.input.value = '';
    this.grow();
  }

  // The orb in the header shows what the bot is doing, as the big one on Home does.
  showState(state) {
    const next = ['idle', 'listening', 'thinking', 'speaking'].includes(state) ? state : 'idle';
    if (next === this.orbState) return;
    this.orbState = next;
    const canvas = this.orb.firstChild;
    if (canvas) setOrb(canvas, { state: next });
  }

  // "Looking it up…" while a web lookup runs and no answer has started yet.
  lookupNote(on) {
    const r = this.pending;
    if (!r) return;
    if (!r.note) {
      r.note = h('span', { class: 'lookup-note' }, 'Looking it up…');
      r.col.insertBefore(r.note, r.dots);
    }
    r.note.hidden = !on;
  }

  addMine(text) {
    const body = h('div', { class: 'body' });
    setText(body, text);
    this.add(h('div', { class: 'msg user' }, body));
  }

  restart() {
    this.voice.cancel();
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
    this.voice.cancel();
    this.conv.close();
    this.el.remove();
  }
}

ChatView.voiceOff = false; // set once a server refuses voice on a chat
