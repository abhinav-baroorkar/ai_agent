// Boot: read who the assistant is from /api/health, then wire the three screens.
import { api } from './api.js';
import { $ } from './dom.js';
import { cleanHealth } from './clean.js';
import { initHome } from './home.js';
import { initBots } from './bots.js';
import { initSettings } from './settings.js';
import { miniOrb } from './faces/mini-orb.js';

const VIEWS = ['home', 'bots', 'setup', 'chat', 'settings'];
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

const home = await initHome(info, (online, why) => setOffline(!online, why || undefined));
const bots = initBots(info);
// Home's orb breathes in the main assistant's style (the server only sends one if it has styles)
bots.onChange((list) => {
  const main = list.find((b) => b.id === info.default_bot);
  if (main && main.hasStyle && home.face.setStyle) home.face.setStyle(main.style);
});
const settings = initSettings(info, bots);

function route() {
  let view = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  if (view === 'chat' && !bots.hasChat) view = 'bots'; // no chat in front: show the bots instead
  $('#home').hidden = view !== 'home';
  $('#work').hidden = view === 'home';
  for (const id of ['bots', 'setup', 'chat', 'settings']) $(`#${id}`).hidden = view !== id;
  $('#to-bots').hidden = view === 'bots';
  $('#to-settings').hidden = view === 'settings';
  if (view === 'home' || view === 'bots') bots.load();
  if (view === 'setup') bots.showSetup(); else bots.leaveSetup();
  if (view === 'settings') bots.load().then(settings.load);
}

window.addEventListener('hashchange', route);
$('#mini-face').append(miniOrb([243, 201, 139], 34)); // the assistant's own orb on the way back
route();
