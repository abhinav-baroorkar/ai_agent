// Making a bot in three plain steps (what is it for, what may it use, name it),
// and the Change screen for one that exists, which shows the same blocks on one
// page plus a way to delete it.
import { api } from './api.js';
import { $, h, clear } from './dom.js';
import { cleanModel } from './clean.js';
import { STARTS, useWord } from './bot-look.js';
import { miniOrb } from './faces/mini-orb.js';
import { stylePicker } from './style-picker.js';
import { colorOf } from './bot-look.js';

const START_COLORS = { web: [114, 214, 196], write: [179, 166, 245], blank: [140, 196, 245] };

const WHY_ONE = (name) => 'Why only one? A web page can contain hidden instructions. A bot that reads the web is kept away ' +
  `from your home and your accounts, so a page can’t tell it to do anything there. ${name} itself can’t browse the web yet; ` +
  'a separate helper for that is planned but not built.';

export function initSetup({ health, lists, saved, deleted }) {
  const root = $('#setup');
  let s = null; // the bot being made or changed
  let models = [];

  function begin({ bot, start }) {
    const { bots, connectors, mainId, styles } = lists();
    const main = bots.find((b) => b.id === mainId);
    const browser = connectors.find((c) => c.kind === 'browser');
    const own = connectors.filter((c) => c.kind !== 'browser');
    // from a card's starting point we skip to step 2; from scratch, step 1 opens on the first one
    const preset = STARTS.find((x) => x.id === start) || (bot ? null : STARTS[0]);
    const skip = !!start;
    s = {
      mainId, styles, bot: bot || null, step: bot ? 0 : skip ? 2 : 1, start: preset ? preset.id : 'web', browser, own,
      name: bot ? bot.name : preset ? preset.name : '', prompt: bot ? bot.system_prompt : preset ? preset.prompt : '',
      model: bot ? bot.model : main ? main.model : '', error: '', sure: false, style: bot ? bot.style : preset ? preset.style || 'glow' : 'glow',
      use: bot ? (browser && bot.connectors.includes(browser.id) ? 'web' : bot.connectors.length ? 'own' : 'none') : preset ? preset.use : 'none',
      picked: new Set(bot ? bot.connectors.filter((id) => own.some((c) => c.id === id)) : own.filter((c) => c.enabled).map((c) => c.id)),
    };
    if (s.use === 'web' && (!browser || !browser.enabled) && !bot) s.use = 'none';
    api('GET', '/api/models').then((r) => { models = (r.models || []).map(cleanModel).filter((m) => m.name); render(); }).catch(() => {});
    location.hash = '#setup';
    render();
  }

  // --- the three blocks ---------------------------------------------------------
  function purposeBlock() {
    return h('div', { class: 'choices', role: 'radiogroup', 'aria-label': 'Starting point' }, ...STARTS.map((x) => h('button', {
      class: 'choice', type: 'button', role: 'radio', 'aria-checked': String(s.start === x.id),
      onclick: () => { Object.assign(s, { start: x.id, name: x.name, prompt: x.prompt, style: x.style || 'glow', use: x.use === 'web' && !(s.browser && s.browser.enabled) ? 'none' : x.use }); render(); },
    }, miniOrb(x.id === 'web' ? [114, 214, 196] : x.id === 'write' ? [179, 166, 245] : [140, 196, 245], 56), h('h3', {}, x.title), h('p', {}, x.about))));
  }

  function usesBlock() {
    const option = (id, title, text, disabled = false) => h('button', {
      class: 'option', type: 'button', role: 'radio', 'aria-checked': String(s.use === id), disabled,
      onclick: () => { s.use = id; render(); },
    }, h('span', { class: 'dot' }), h('span', {}, h('h3', {}, title), h('p', {}, text)));
    const ownWords = s.own.map(useWord);
    const ownTitle = ownWords.length ? ownWords.map((w, i) => (i ? w : w.charAt(0).toUpperCase() + w.slice(1))).join(', ') : '';
    const rows = [option('none', 'Nothing extra', 'It just talks with you.')];
    if (s.browser) {
      rows.push(s.browser.enabled
        ? option('web', 'The web', 'It can search and read pages in its own browser. You can watch it and stop it at any time.')
        : option('web', 'The web', 'The browser is switched off. Turn it on in Settings first.', true));
    }
    if (s.own.length) rows.push(option('own', ownTitle, s.own.length === 1 && s.own[0].kind === 'home_assistant' ? 'It can check and switch the devices you have allowed.' : 'It can use the things you tick below.'));
    const out = [h('div', { class: 'options', role: 'radiogroup', 'aria-label': 'What it may use' }, ...rows)];
    if (s.use === 'own' && s.own.length > 1) {
      out.push(h('div', { class: 'own-list' }, ...s.own.map((c) => h('label', { class: 'check' },
        h('input', { type: 'checkbox', checked: s.picked.has(c.id), onchange: (ev) => { if (ev.target.checked) s.picked.add(c.id); else s.picked.delete(c.id); } }),
        useWord(c).charAt(0).toUpperCase() + useWord(c).slice(1), !c.enabled && h('small', {}, 'switched off in Settings')))));
    }
    if (s.browser) out.push(h('p', { class: 'why-one' }, WHY_ONE(health.assistant.name)));
    return out;
  }

  function nameBlock() {
    const locked = !!(s.bot && s.bot.builtin);
    const names = models.map((m) => m.name);
    if (s.model && !names.includes(s.model)) names.unshift(s.model);
    return h('div', { class: 'fields' },
      h('label', { for: 'setup-name' }, 'Its name'),
      h('input', { id: 'setup-name', type: 'text', maxLength: 40, value: s.name, disabled: locked, placeholder: 'Recipe helper', oninput: (ev) => { s.name = ev.target.value; } }),
      h('label', { for: 'setup-prompt' }, 'What it should do, in your own words'),
      h('textarea', { id: 'setup-prompt', rows: 5, value: s.prompt, disabled: locked, placeholder: 'What this bot is for and how it should answer.', oninput: (ev) => { s.prompt = ev.target.value; } }),
      locked && h('p', { class: 'locked' }, s.bot.id === s.mainId ? 'The main assistant’s name and instructions are set in its own settings file, not here.'
        : `This is the bot that answers the wake word. Its name and instructions follow ${health.assistant.name}’s; here you choose what it may use and which model it runs on.`),
      h('details', {}, h('summary', {}, 'More: which AI model it runs on'),
        h('select', { id: 'setup-model', 'aria-label': 'AI model', onchange: (ev) => { s.model = ev.target.value; } }, ...names.map((n) => {
          const m = models.find((x) => x.name === n);
          return h('option', { value: n, selected: n === s.model }, m ? `${n}  (${m.size_gb.toFixed(1)} GB${m.tools ? '' : ', can only talk'})` : n);
        }))));
  }

  // The server's refusals are written for developers; say the two about styles plainly.
  function plain(message) {
    if (/^style: /.test(message)) return 'That breathing style isn’t one the assistant knows. Pick one of the eight and save again.';
    if (/wake-word bot shows the main bot's style/.test(message)) {
      return `The wake-word bot always looks like ${health.assistant.name}. Change ${health.assistant.name}’s style instead.`;
    }
    return message;
  }

  function styleBlock() {
    const color = s.bot ? colorOf(s.bot) : START_COLORS[s.start] || START_COLORS.blank;
    return stylePicker({ color, value: s.style, onPick: (id) => { s.style = id; } });
  }

  // --- saving and deleting ---------------------------------------------------------
  function body() {
    const ids = s.use === 'web' && s.browser ? [s.browser.id] : s.use === 'own' ? s.own.filter((c) => s.own.length === 1 || s.picked.has(c.id)).map((c) => c.id) : [];
    const out = { model: s.model, connectors: ids };
    // only to a server that knows the field; the wake-word bot has no style of its own
    if (s.styles && !(s.bot && s.bot.builtin && s.bot.id !== s.mainId)) out.style = s.style;
    if (!(s.bot && s.bot.builtin)) Object.assign(out, { name: s.name.trim(), system_prompt: s.prompt.trim() });
    return out;
  }

  async function save() {
    if (!(s.bot && s.bot.builtin) && !s.name.trim()) { s.error = 'Give it a name first.'; return render(); }
    try {
      const bot = s.bot ? await api('PATCH', `/api/bots/${encodeURIComponent(s.bot.id)}`, body()) : await api('POST', '/api/bots', body());
      const isNew = !s.bot;
      s = null;
      await saved(bot, isNew);
    } catch (err) {
      s.error = plain(err.message);
      render();
    }
    return undefined;
  }

  async function remove() {
    if (!s.sure) { s.sure = true; return render(); } // first click asks, second deletes
    try {
      const id = s.bot.id;
      await api('DELETE', `/api/bots/${encodeURIComponent(id)}`);
      s = null;
      await deleted(id);
    } catch (err) {
      s.error = err.message;
      s.sure = false;
      render();
    }
    return undefined;
  }

  function render() {
    if (!s) return;
    const error = s.error && h('p', { class: 'form-error', role: 'alert' }, s.error);
    const cancel = h('a', { class: 'btn quiet', href: '#bots' }, 'Cancel');
    if (s.bot) { // Change: everything on one page
      clear(root).append(...[h('h1', {}, `Change ${s.bot.name}`),
        h('section', {}, h('h2', {}, 'What it is called and what it does'), nameBlock()),
        h('section', {}, h('h2', {}, 'What it may use'), ...usesBlock()),
        s.styles && (!s.bot.builtin || s.bot.id === s.mainId) && h('section', {}, h('h2', {}, 'How it breathes'),
          s.bot.id === s.mainId && h('p', { class: 'lede' }, 'This is the orb on the first screen. The wake word answers in the same style.'), styleBlock()),
        error, h('div', { class: 'setup-foot' }, h('button', { class: 'btn primary big', type: 'button', onclick: save }, 'Save changes'), cancel),
        !s.bot.builtin && h('section', { class: 'danger-zone' },
          h('p', {}, s.sure ? `Delete ${s.bot.name}? Its instructions and its open chat are removed. This can’t be undone.` : 'Finished with this bot? You can delete it.'),
          h('button', { class: 'btn danger', type: 'button', onclick: remove }, s.sure ? `Yes, delete ${s.bot.name}` : 'Delete this bot'),
          s.sure && h('button', { class: 'btn quiet', type: 'button', onclick: () => { s.sure = false; render(); } }, 'Keep it'))].filter(Boolean)); // parts that don't apply to this bot are left out
      return;
    }
    const all = [
      ['What is it for', 'What should this bot help with?', 'Pick a starting point. You can change everything afterwards.', () => [purposeBlock()]],
      ['What may it use', 'What may it use?', 'Choose one. Everything runs on this computer.', () => usesBlock()],
      ['How it breathes', 'How should it breathe?', 'Pick how its orb moves. It changes only the look, not what the bot does.', () => [styleBlock()]],
      ['Name it', 'Name it', 'Give it a name and say what it should do. You can change this later.', () => [nameBlock()]],
    ];
    const plan = s.styles ? all : all.filter((x) => x[0] !== 'How it breathes'); // a server without styles: three steps
    const [, title, sub] = plan[s.step - 1];
    const steps = h('div', { class: 'steps' }, ...plan.flatMap(([t], i) => [i ? h('i') : null, h(i + 1 === s.step ? 'b' : 'span', {}, `${i + 1} ${t}`)]));
    const block = plan[s.step - 1][3]();
    s.last = plan.length;
    const back = s.step > 1 ? h('button', { class: 'btn quiet', type: 'button', onclick: () => { s.step -= 1; s.error = ''; render(); } }, 'Back') : cancel;
    const next = s.step < s.last
      ? h('button', { class: 'btn primary big', type: 'button', onclick: () => { s.step += 1; render(); } }, 'Next')
      : h('button', { class: 'btn primary big', type: 'button', onclick: save }, 'Make this bot');
    clear(root).append(steps, h('h1', {}, title), h('p', { class: 'lede' }, sub), ...block, error,
      h('div', { class: 'setup-foot' }, back, h('span', { class: 'grow' }), next));
  }

  return {
    begin,
    show() { if (!s) begin({}); else render(); }, // arriving at #setup with nothing started: a new bot from step 1
    leave() { s = null; }, // going anywhere else abandons what was half filled in
  };
}
