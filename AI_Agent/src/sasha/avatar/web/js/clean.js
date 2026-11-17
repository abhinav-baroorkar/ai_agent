// Everything the server sends is checked into a known shape here, once, so
// the rest of the UI can call string and array methods without guarding.
import { STYLE_IDS } from './faces/styles.js';

const str = (v, fallback = '') => (typeof v === 'string' ? v : v == null ? fallback : String(v));
const list = (v) => (Array.isArray(v) ? v.map((x) => str(x)) : []);

export function cleanBot(b) {
  const bot = b && typeof b === 'object' ? b : {};
  return {
    id: str(bot.id), name: str(bot.name, 'Unnamed bot') || 'Unnamed bot', model: str(bot.model, 'unknown'),
    system_prompt: str(bot.system_prompt), connectors: list(bot.connectors), builtin: bot.builtin === true,
    style: STYLE_IDS.includes(bot.style) ? bot.style : 'glow', // how its orb breathes
    hasStyle: typeof bot.style === 'string', // the server knows about styles (it refuses fields it doesn't)
  };
}

export function cleanConnector(c) {
  const con = c && typeof c === 'object' ? c : {};
  const settings = con.settings && typeof con.settings === 'object' && !Array.isArray(con.settings) ? con.settings : {};
  return {
    id: str(con.id), kind: str(con.kind), name: str(con.name, 'Connector') || 'Connector', enabled: con.enabled === true,
    status: str(con.status, 'error'), detail: str(con.detail), tools: list(con.tools), settings, secret_env: list(con.secret_env),
  };
}

export function cleanModel(m) {
  const model = m && typeof m === 'object' ? m : {};
  const size = Number(model.size_gb);
  return { name: str(model.name), size_gb: Number.isFinite(size) ? size : 0, tools: model.tools === true };
}

export function cleanHealth(hl) {
  const health = hl && typeof hl === 'object' ? hl : {};
  const a = health.assistant && typeof health.assistant === 'object' ? health.assistant : {};
  const v = health.voice && typeof health.voice === 'object' ? health.voice : {};
  const max = Number(health.limits && health.limits.max_sockets);
  return {
    assistant: { name: str(a.name, 'Assistant') || 'Assistant', pronouns: a.pronouns },
    profile: str(health.profile), default_bot: str(health.default_bot, 'main') || 'main',
    voice: { stt: v.stt === true, tts: v.tts === true, tts_languages: list(v.tts_languages) },
    // One socket is Home's; the rest are for bot chats. 4 is the server's default.
    max_chats: Math.max(1, (Number.isInteger(max) && max > 1 ? max : 4) - 1),
  };
}

// A site name is shown only if it is a plain ASCII hostname (which includes punycode,
// "xn--..."): letters that merely look like another site's can't get through.
const HOSTNAME = /^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;
export const safeSite = (site) => (site === '' ? '' : HOSTNAME.test(site) ? site : '(unusual address)');

// What the assistant's browser is doing. Sites and titles come from web pages.
export function cleanBrowserState(b) {
  const s = b && typeof b === 'object' ? b : {};
  const g = s.guard && typeof s.guard === 'object' ? s.guard : {};
  const count = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Math.floor(Number(v)) : 0);
  const who = s.holder_bot && typeof s.holder_bot === 'object' ? str(s.holder_bot.name).slice(0, 60) : '';
  const via = s.holder_bot && s.holder_bot.via === 'helper' ? 'helper' : 'direct'; // v0.5.1: the main assistant's web lookup
  const MODES = ['assistant', 'waiting', 'switching', 'owner', 'restoring'];
  const until = Date.parse(str(s.owner_until));
  const ownerRefused = {};
  if (s.owner_refused && typeof s.owner_refused === 'object') for (const [k, v] of Object.entries(s.owner_refused)) ownerRefused[str(k)] = count(v);
  const refused = {};
  if (g.refused && typeof g.refused === 'object') for (const [k, v] of Object.entries(g.refused)) refused[str(k)] = count(v);
  return {
    enabled: s.enabled === true, running: s.running === true, agent: s.agent === 'working' ? 'working' : 'idle', user: who, // the bot using it, if the server says
    tabs: (Array.isArray(s.tabs) ? s.tabs : []).filter((t) => t && typeof t === 'object').slice(0, 20).map((t) => ({
      tab: count(t.tab), site: safeSite(str(t.site).slice(0, 255)), title: str(t.title).slice(0, 80), current: t.current === true,
    })),
    guard: { allowed: count(g.allowed), refused, background_refused: count(g.background_refused) },
    via,
    // take-over (v0.5); mode stays null for a server without it, so nothing about it shows
    mode: MODES.includes(s.mode) ? s.mode : null, owner_until: Number.isFinite(until) ? until : 0, owner_refused: ownerRefused,
    // `restoring`: why the clean-up after his window keeps failing (after 3 tries), in plain words
    restore_problem: typeof s.restore_problem === 'string' ? s.restore_problem.slice(0, 200) : '',
    // a search met the person check and waits for him (cleared by the server: a search that
    // works, or a take-over whose window came up); a yes/no only
    person_check: s.person_check === true,
  };
}

// A reply's own tool_calls ([{name, ok, asks?}], sent by the server with the reply) decide
// whether its lookup met the search site's person check: a web tool failed with the fixed
// label asks === "person_check" and none worked. Never from text; nothing carries over.
export function metPersonCheck(calls, isWebTool) {
  const web = (Array.isArray(calls) ? calls : []).filter((c) => c && typeof c === 'object' && isWebTool(str(c.name)));
  return web.some((c) => c.ok === false && c.asks === 'person_check') && !web.some((c) => c.ok === true);
}

// reply.sites (v0.5.1): the hostnames a web lookup really used, joined for one line of
// text. Only plain hostnames are kept, at most five; null when there are none.
export function lookedUp(sites) {
  const list = (Array.isArray(sites) ? sites : []).map((x) => str(x).slice(0, 255)).map(safeSite)
    .filter((x) => x && x !== '(unusual address)').slice(0, 5);
  return list.length ? list.join(', ') : null;
}

const KINDS = ['timer', 'alarm', 'reminder'];
const TIMER_STATES = ['scheduled', 'ringing', 'pending', 'missed', 'done'];

// A timer, alarm or reminder. Returns null for anything without an id or a readable time.
export function cleanTimer(t) {
  const item = t && typeof t === 'object' ? t : {};
  const due = Date.parse(str(item.due_at));
  if (!str(item.id) || !Number.isFinite(due)) return null;
  const late = Number(item.late_s);
  return {
    id: str(item.id), kind: KINDS.includes(item.kind) ? item.kind : 'timer', label: str(item.label), text: str(item.text),
    due, repeat: ['daily', 'weekdays', 'weekends'].includes(item.repeat) ? item.repeat : 'none',
    state: TIMER_STATES.includes(item.state) ? item.state : 'scheduled', late_s: Number.isFinite(late) && late > 0 ? late : 0,
    voice: item.created_by === 'wake',
  };
}

export const initial = (name) => str(name).trim().charAt(0).toUpperCase() || '?';

// Memory (contract v0.6). A note's text is the owner's own words: shown as text only.
export function cleanNote(n) {
  const note = n && typeof n === 'object' ? n : {};
  const at = Number(note.created_at);
  return {
    id: str(note.id), text: str(note.text), created_at: Number.isFinite(at) ? at : 0,
    source: ['chat', 'window', 'wake'].includes(note.source) ? note.source : 'chat', bot_id: str(note.bot_id),
    pending: note.status === 'pending', say_aloud: note.say_aloud === true,
  };
}
export function cleanMemory(m) {
  const mem = m && typeof m === 'object' ? m : {};
  const notes = Array.isArray(mem.notes) ? mem.notes.map(cleanNote).filter((n) => n.id) : [];
  const max = Number(mem.max);
  return { notes, count: Number.isFinite(Number(mem.count)) ? Number(mem.count) : notes.length, max: Number.isFinite(max) && max > 0 ? max : 200 };
}
