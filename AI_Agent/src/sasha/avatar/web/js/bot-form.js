// The "New bot" / "Edit bot" dialog. Resolves to {bot}, {deleted: id} or null.
import { api } from './api.js';
import { $, h, clear } from './dom.js';

// preferModel: what a new bot starts on (the main assistant's model, which is already loaded).
export function openBotForm({ bot, models, connectors, preferModel }) {
  const dlg = $('#bot-dialog');
  const form = $('#bot-form');
  const f = {
    title: $('#bot-dialog-title'), name: $('#bot-name'), model: $('#bot-model'), prompt: $('#bot-prompt'),
    conns: $('#bot-connectors'), error: $('#bot-error'), del: $('#bot-delete'), cancel: $('#bot-cancel'), save: $('#bot-save'),
  };
  const builtin = !!(bot && bot.builtin);

  f.title.textContent = bot ? `Edit ${bot.name}` : 'New bot';
  f.save.textContent = bot ? 'Save changes' : 'Create bot';
  f.save.disabled = false;
  f.name.value = bot ? bot.name : '';
  f.prompt.value = bot ? bot.system_prompt : '';
  // The main assistant's name and instructions come from its config file.
  f.name.disabled = f.prompt.disabled = builtin;
  f.name.title = f.prompt.title = builtin ? 'Set in config/assistant.yaml' : '';
  f.error.hidden = true;
  f.del.hidden = !bot || builtin;
  f.del.textContent = 'Delete bot';
  let armed = false;

  const names = models.map((m) => m.name);
  if (bot && !names.includes(bot.model)) names.unshift(bot.model);
  clear(f.model).append(...names.map((name) => {
    const m = models.find((x) => x.name === name);
    const label = m ? `${name}  (${m.size_gb.toFixed(1)} GB${m.tools ? '' : ', can’t use connectors'})` : `${name}  (not installed)`;
    return h('option', { value: name, selected: name === (bot ? bot.model : preferModel) }, label);
  }));
  if (!names.length) f.model.append(h('option', { value: '', disabled: true, selected: true }, 'No local models found'));

  clear(f.conns).append(h('legend', {}, 'Connectors it may use'));
  if (!connectors.length) f.conns.append(h('p', { class: 'check' }, h('small', {}, 'No connectors yet. Add them in Settings.')));
  for (const c of connectors) {
    const note = c.status === 'ok' ? `${c.tools.length} tools` : c.status === 'off' ? 'switched off' : c.status === 'needs_setup' ? 'needs setup' : 'has an error';
    const alone = c.kind === 'browser' ? ', must be the only connector' : '';
    f.conns.append(h('label', { class: 'check' },
      h('input', { type: 'checkbox', value: c.id, checked: bot ? bot.connectors.includes(c.id) : false,
        dataset: { kind: c.kind }, onchange: exclusive }),
      c.name, h('small', {}, note + alone)));
  }
  exclusive();

  // A bot that browses can have no other connector (the server enforces it too):
  // ticking Browser clears and locks the rest, so the form can't offer a refused mix.
  function exclusive() {
    const boxes = [...f.conns.querySelectorAll('input')];
    const browsing = boxes.some((b) => b.dataset.kind === 'browser' && b.checked);
    for (const b of boxes) {
      if (b.dataset.kind === 'browser') continue;
      if (browsing) b.checked = false;
      b.disabled = browsing;
    }
  }

  return new Promise((resolve) => {
    const done = (result) => {
      form.onsubmit = f.del.onclick = f.cancel.onclick = dlg.oncancel = null;
      dlg.close();
      resolve(result);
    };
    const fail = (err) => {
      f.error.textContent = err.message;
      f.error.hidden = false;
      f.save.disabled = false;
    };
    f.cancel.onclick = () => done(null);
    dlg.oncancel = () => done(null);
    f.del.onclick = async () => {
      if (!armed) {
        armed = true;
        f.del.textContent = 'Really delete?';
        return;
      }
      try {
        await api('DELETE', `/api/bots/${encodeURIComponent(bot.id)}`);
        done({ deleted: bot.id });
      } catch (err) { fail(err); }
    };
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const body = {
        model: f.model.value,
        connectors: [...f.conns.querySelectorAll('input:checked')].map((i) => i.value),
      };
      if (!builtin) Object.assign(body, { name: f.name.value.trim(), system_prompt: f.prompt.value.trim() });
      f.save.disabled = true;
      try {
        const saved = bot
          ? await api('PATCH', `/api/bots/${encodeURIComponent(bot.id)}`, body)
          : await api('POST', '/api/bots', body);
        done({ bot: saved });
      } catch (err) { fail(err); }
    };
    dlg.showModal();
    (builtin ? f.model : f.name).focus();
  });
}
