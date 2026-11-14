// Everything the server sends is checked into a known shape here, once, so
// the rest of the UI can call string and array methods without guarding.
const str = (v, fallback = '') => (typeof v === 'string' ? v : v == null ? fallback : String(v));
const list = (v) => (Array.isArray(v) ? v.map((x) => str(x)) : []);

export function cleanBot(b) {
  const bot = b && typeof b === 'object' ? b : {};
  return {
    id: str(bot.id), name: str(bot.name, 'Unnamed bot') || 'Unnamed bot', model: str(bot.model, 'unknown'),
    system_prompt: str(bot.system_prompt), connectors: list(bot.connectors), builtin: bot.builtin === true,
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
  return { name: str(model.name), size_gb: Number.isFinite(size) ? size : 0, tools: model.tools !== false };
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

export const initial = (name) => str(name).trim().charAt(0).toUpperCase() || '?';
