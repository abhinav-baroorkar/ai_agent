// In-browser stand-in for the brain server, used with ?mock=1.
// Follows Dependencies/team/api-contract.md (v0) message for message.
import * as data from './mock-data.js';
import * as timers from './mock-timers.js';
import * as memory from './mock-memory.js';
import * as browser from './mock-browser.js';

export { MockBrowserSocket } from './mock-browser.js';

const params = new URLSearchParams(location.search);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const copy = (x) => JSON.parse(JSON.stringify(x));
let nextId = 1;
// the server's own list and its exact message for a style it doesn't know
const STYLE_NAMES = ['glow', 'ember', 'tide', 'gale', 'storm', 'mist', 'bloom', 'stone'];
const STYLE_ERROR = `style: Input should be ${STYLE_NAMES.slice(0, -1).map((n) => `'${n}'`).join(', ')} or 'stone'`;

function fail(status, error) {
  const err = new Error(error);
  err.status = status;
  return err;
}

export async function mockFetch(method, path, body) {
  await wait(120);
  const [, , kind, id, action] = path.split('/'); // /api/<kind>/<id>/<action>
  if (kind === 'health') return copy(data.health);
  if (kind === 'models') return { models: copy(data.models) };
  if (kind === 'bots') { // as the server answers (contract v0.4.4)
    const bot = data.bots.find((b) => b.id === id);
    const main = data.bots.find((b) => b.id === 'main');
    // the wake-word bot has no style of its own: it shows the main bot's
    const shown = (b) => (b.id === 'voice' ? { ...b, style: main.style } : b);
    const badStyle = (fields) => 'style' in fields && !STYLE_NAMES.includes(fields.style);
    if (method === 'GET') return { bots: copy(data.bots.map(shown)) };
    if (method === 'POST') {
      if (!body.name) throw fail(400, 'name: required');
      if (badStyle(body)) throw fail(400, STYLE_ERROR);
      const made = { id: `b-${nextId++}`, system_prompt: '', connectors: [], ...body, style: body.style || 'glow', builtin: false };
      data.bots.push(made);
      return copy(made);
    }
    if (!bot) throw fail(404, 'That bot no longer exists.');
    if (method === 'PATCH') {
      if (badStyle(body)) throw fail(400, STYLE_ERROR);
      if ('style' in body && bot.id === 'voice') throw fail(400, "The wake-word bot shows the main bot's style; set it on main");
      const fixed = ['name', 'system_prompt'].filter((k) => k in body);
      if (bot.builtin && fixed.length) throw fail(400, `The built-in bot's ${fixed.join(' and ')} come from assistant.yaml`);
      return copy(shown(Object.assign(bot, body)));
    }
    if (method === 'DELETE') {
      if (bot.builtin) throw fail(400, 'The main assistant can’t be deleted.');
      data.bots.splice(data.bots.indexOf(bot), 1);
      return { ok: true };
    }
  }
  if (kind === 'connectors') {
    const c = data.connectors.find((x) => x.id === id);
    if (method === 'GET') return { connectors: copy(data.connectors) };
    if (!c) throw fail(404, 'That connector no longer exists.');
    if (method === 'PUT') {
      if ('enabled' in body) {
        c.enabled = body.enabled;
        c.prev = c.enabled ? c.prev : c.status;
        c.status = c.enabled ? (c.prev && c.prev !== 'off' ? c.prev : 'ok') : 'off';
      }
      return copy(c);
    }
    if (action === 'test') {
      await wait(700);
      if (c.status === 'ok') return { ok: true, message: 'Reached it in 41 ms.' };
      if (c.status === 'needs_setup') return { ok: false, message: `${c.secret_env[0]} is not set.` };
      return { ok: false, message: c.detail };
    }
  }
  if (kind === 'timers') return timers.timersFetch(method, id, action, body, fail);
  if (kind === 'memory') return memory.memoryFetch(method, id, action, body, fail);
  if (kind === 'browser') {
    const answer = browser.browserFetch(method, id, fail);
    if (answer) return answer;
  }
  throw fail(404, 'Not found.');
}

// Typed text is matched to the scripted turn it is about, so the answer fits the question.
function pick(text, sock) {
  const t = text.toLowerCase();
  const hit = data.script.find((s) => s.keys.every((k) => t.includes(k)));
  return hit || { heard: text, tool: null, reply: `This is the mock, so I only know a few lines. Try: “${data.script[sock.turn++ % data.script.length].heard}”.` };
}

export class MockSocket {
  static open = 0;

  constructor(botId, speak = false) {
    this.botId = botId;
    this.speaks = speak;
    this.turn = 0;
    if (MockSocket.open >= data.health.limits.max_sockets) { // same cap as the real server
      setTimeout(() => this.onclose && this.onclose({ code: 1013 }), 100);
      return;
    }
    MockSocket.open++;
    this.counted = true;
    this.timers = new Set();
    this.heardTimer = null;
    this.run = 0;
    setTimeout(() => this.onopen && this.onopen(), 150);
    timers.attach(this);
    memory.attach(this);
    // ?mock=1&ring=1 makes the first timer ring a few seconds after the page opens.
    if (params.has('ring') && speak) setTimeout(() => timers.ring(params.get('ring')), 3500);
    // ?mock=1&drop=1 drops the first connection once, to show the offline state.
    if (params.has('drop') && !MockSocket.dropped) {
      MockSocket.dropped = true;
      setTimeout(() => this.close(), 9000);
    }
  }

  emit(msg) {
    if (this.onmessage) this.onmessage({ data: msg instanceof ArrayBuffer ? msg : JSON.stringify(msg) });
  }

  later(ms) {
    const run = this.run;
    return new Promise((resolve) => {
      const t = setTimeout(() => {
        this.timers.delete(t);
        if (run === this.run) resolve();
      }, ms);
      this.timers.add(t);
    });
  }

  stopAll() {
    this.run++;
    this.timers.forEach(clearTimeout);
    this.timers.clear();
    clearInterval(this.heardTimer);
  }

  close() {
    timers.detach(this);
    memory.detach(this);
    if (this.counted) MockSocket.open--;
    this.counted = false;
    this.stopAll();
    setTimeout(() => this.onclose && this.onclose(), 0);
  }

  send(raw) {
    if (typeof raw !== 'string') return; // mic frames: the mock ignores the sound
    const msg = JSON.parse(raw);
    timers.onFrame(msg);
    // a message to a bot that may browse puts the mock browser to work
    const bot = data.bots.find((b) => b.id === this.botId);
    const step = msg.type === 'text' ? pick(msg.text, this) : null;
    if (step && bot && bot.connectors.includes('browser') && !step.noBrowser) browser.use();
    if (step && step.tool && step.tool.asks === 'person_check') browser.personCheckMet();
    if (step) this.answer(step, false);
    if (msg.type === 'audio_start') this.listen();
    if (msg.type === 'audio_stop') this.heardDone();
    if (msg.type === 'cancel' || msg.type === 'reset') {
      this.stopAll();
      this.emit({ type: 'state', state: 'idle' });
    }
  }

  listen() {
    if (!data.health.voice.stt) {
      this.emit({ type: 'error', message: 'Voice is not available on this machine.' });
      return;
    }
    this.stopAll();
    this.step = data.script[this.turn++ % data.script.length];
    this.emit({ type: 'state', state: 'listening' });
    const words = this.step.heard.split(' ');
    let n = 0;
    this.heardTimer = setInterval(() => {
      if (n >= words.length) return clearInterval(this.heardTimer);
      this.emit({ type: 'transcript', text: words.slice(0, ++n).join(' '), final: false });
    }, 330);
  }

  heardDone() {
    if (!this.step) return;
    clearInterval(this.heardTimer);
    const step = this.step;
    this.step = null;
    this.emit({ type: 'transcript', text: step.heard, final: true });
    this.answer(step, true);
  }

  // Runs one whole spoken turn by itself: used by the Home screen's demo.
  async demo() {
    this.listen();
    await this.later(330 * (this.step.heard.split(' ').length + 2));
    this.heardDone();
  }

  async answer(step, spoken) {
    if (!spoken) this.stopAll();
    const started = performance.now();
    this.emit({ type: 'state', state: 'thinking' });
    await this.later(1300);
    if (step.tool) {
      this.emit({ type: 'tool', ...step.tool });
      if (step.tool.name === 'browse') browser.use(true); // a web lookup holds the browser
      await this.later(step.tool.name === 'browse' ? 3500 : 600);
    }
    const words = step.reply.split(' ');
    for (let i = 0; i < words.length; i++) {
      this.emit({ type: 'reply_delta', text: (i ? ' ' : '') + words[i] });
      await this.later(55);
    }
    const total = Math.round(performance.now() - started);
    this.emit({ type: 'reply', text: step.reply, tool_calls: step.tool ? [step.tool] : [], sites: step.sites || [], timing: { total_ms: total, tokens_per_s: 38.4 } });
    const lang = /[\u0900-\u097F]/.test(step.reply) ? 'mr' : 'en';
    if (this.speaks && data.health.voice.tts && data.health.voice.tts_languages.includes(lang)) await this.speak(step.reply);
    this.emit({ type: 'state', state: 'idle' });
  }

  // Fake speech: a hum whose loudness rises and falls like syllables. The
  // player is muted in mock mode; only the loudness is used, to move the mouth.
  async speak(text) {
    const rate = 16000;
    const frames = Math.min(300, Math.round(text.length * 3.2));
    this.emit({ type: 'state', state: 'speaking' });
    this.emit({ type: 'tts_start', sample_rate: rate });
    for (let f = 0; f < frames; f++) {
      const pcm = new Int16Array(320);
      const t0 = f * 0.02;
      const syll = Math.abs(Math.sin(t0 * 11)) * (0.45 + 0.55 * Math.abs(Math.sin(t0 * 2.3 + 1)));
      const pause = Math.sin(t0 * 1.9) > 0.93 ? 0.05 : 1;
      for (let i = 0; i < 320; i++) {
        pcm[i] = Math.sin(2 * Math.PI * 170 * (t0 + i / rate)) * syll * pause * 9000;
      }
      this.emit(pcm.buffer);
      if (f % 10 === 9) await this.later(60); // arrives about 3x faster than real time
    }
    this.emit({ type: 'tts_end' });
  }
}
