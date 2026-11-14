// Settings screen: what the main assistant runs on, and the connector cards.
// Secrets are never typed here: a card only names the variable to fill in.
import { api } from './api.js';
import { $, h, clear } from './dom.js';

const STATUS = { ok: 'Working', off: 'Off', needs_setup: 'Needs setup', error: 'Problem' };
const SECRETS_FILE = '.env';

function fact(label, value, mono = false, extra = null) {
  return h('div', {}, h('dt', {}, label), h('dd', { class: mono ? 'mono' : '' }, value, extra));
}

function codeList(names) {
  return names.flatMap((v, i) => [i ? ' and ' : '', h('code', {}, v)]);
}

export function initSettings(health, botsApi) {
  const facts = $('#assistant-facts');
  const grid = $('#connector-grid');

  function renderFacts() {
    const main = botsApi.bots.find((b) => b.id === health.default_bot);
    const p = health.assistant.pronouns;
    const pronouns = p && typeof p === 'object' ? `${p.subject} / ${p.object}` : p || 'not set';
    clear(facts).append(
      fact('Name', health.assistant.name),
      fact('Pronouns', pronouns),
      fact('Model', main ? main.model : 'unknown', true,
        main && h('button', { class: 'btn small quiet', type: 'button', style: 'margin-left:.5rem', onclick: () => botsApi.edit(main) }, 'Change')),
      fact('Hardware profile', health.profile || 'unknown'),
      fact('Hearing (speech to text)', health.voice.stt ? 'On' : 'Not set up'),
      fact('Speaking (text to speech)', health.voice.tts ? 'On' : 'Not set up'));
  }

  function card(c) {
    const secrets = c.secret_env || [];
    const tools = c.tools || [];
    const result = h('span', { class: 'test-result', role: 'status' });
    const testBtn = h('button', { class: 'btn small', type: 'button', disabled: !c.enabled }, 'Test');
    testBtn.onclick = async () => {
      testBtn.disabled = true;
      result.className = 'test-result';
      result.textContent = 'Testing…';
      try {
        const r = await api('POST', `/api/connectors/${encodeURIComponent(c.id)}/test`);
        result.className = `test-result ${r.ok ? 'ok' : 'bad'}`;
        result.textContent = r.message || (r.ok ? 'It works.' : 'It did not answer.');
      } catch (err) {
        result.className = 'test-result bad';
        result.textContent = err.message;
      }
      testBtn.disabled = false;
    };

    const toggle = h('input', { type: 'checkbox', checked: c.enabled, 'aria-label': `Use ${c.name}` });
    toggle.onchange = async () => {
      toggle.disabled = true;
      try {
        await api('PUT', `/api/connectors/${encodeURIComponent(c.id)}`, { enabled: toggle.checked });
      } catch (err) {
        result.className = 'test-result bad';
        result.textContent = err.message;
        toggle.checked = !toggle.checked;
        toggle.disabled = false;
        return;
      }
      await load();
    };

    const needsSecret = c.status === 'needs_setup' && secrets.length > 0;
    const settings = Object.entries(c.settings || {});
    return h('article', { class: `card ${c.enabled ? '' : 'is-off'}` },
      h('div', { class: 'card-head' },
        h('div', {}, h('h3', {}, c.name), h('span', { class: 'kind' }, c.kind)),
        h('span', { class: 'grow' }),
        h('span', { class: 'pill', dataset: { tone: c.status } }, STATUS[c.status] || c.status),
        h('label', { class: 'switch' }, toggle, h('span'))),
      c.detail && h('p', { class: 'detail' }, c.detail),
      needsSecret && h('div', { class: 'setup' },
        h('strong', {}, 'To finish setting this up'),
        h('ol', {},
          h('li', {}, 'Open the ', h('code', {}, SECRETS_FILE), ' file in the project folder.'),
          h('li', {}, 'Fill in ', ...codeList(secrets), '.'),
          h('li', {}, 'Press Test. No restart is needed.'))),
      settings.length > 0 && h('p', { class: 'detail' },
        ...settings.flatMap(([k, v]) => [`${k}: `, h('code', {}, String(v)), ' '])),
      tools.length > 0 && h('div', { class: 'tools', 'aria-label': 'Tools this connector gives' },
        ...tools.map((t) => h('span', { class: 'chip' }, t))),
      !needsSecret && secrets.length > 0 && h('p', { class: 'detail' },
        'Its secret is read from ', ...codeList(secrets), ' in ', h('code', {}, SECRETS_FILE), '.'),
      h('div', { class: 'card-foot' }, testBtn, result));
  }

  async function load() {
    renderFacts();
    let connectors;
    try {
      connectors = (await api('GET', '/api/connectors')).connectors;
    } catch (err) {
      clear(grid).append(h('p', { class: 'form-error' }, err.message));
      return;
    }
    clear(grid).append(...connectors.map(card),
      h('article', { class: 'card note' },
        h('strong', {}, 'Adding your own connectors (MCP) is coming next'),
        `Until then, the connectors above are the ones built in. Secrets always go in the ${SECRETS_FILE} file, never in this window.`));
  }

  botsApi.onChange(renderFacts);
  return { load };
}
