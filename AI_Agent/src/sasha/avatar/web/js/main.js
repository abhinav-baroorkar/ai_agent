// Boot: read who the assistant is from /api/health, then wire the three screens.
import { api } from './api.js';
import { $ } from './dom.js';
import { cleanHealth } from './clean.js';
import { initHome } from './home.js';
import { initBots } from './bots.js';
import { initSettings } from './settings.js';

const VIEWS = ['home', 'bots', 'settings'];
const offline = $('#offline');

function setOffline(isOffline, text = 'Offline. Trying to reconnect…') {
  offline.hidden = !isOffline;
  $('#offline-text').textContent = text;
}

async function health() {
  for (;;) {
    try {
      const data = await api('GET', '/api/health');
      setOffline(false);
      return data;
    } catch {
      setOffline(true, 'Can’t reach the assistant on this computer. Trying again…');
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

const info = cleanHealth(await health());
document.title = info.assistant.name;
document.querySelectorAll('[data-assistant-name]').forEach((el) => { el.textContent = info.assistant.name; });

await initHome(info, (online, why) => setOffline(!online, why || undefined));
const bots = initBots(info);
const settings = initSettings(info, bots);

function route() {
  const view = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  $('#home').hidden = view !== 'home';
  $('#work').hidden = view === 'home';
  $('#bots').hidden = view !== 'bots';
  $('#settings').hidden = view !== 'settings';
  $('#bot-list').hidden = view !== 'bots';
  document.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (view === 'bots') bots.load();
  if (view === 'settings') bots.load().then(settings.load);
}

window.addEventListener('hashchange', route);
route();
