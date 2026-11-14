// Made-up data for ?mock=1. Nothing here is real: device names, tools and
// models are examples only.
const params = new URLSearchParams(location.search);

export const health = {
  ok: true,
  assistant: { name: params.get('name') || 'Mira', pronouns: { subject: 'she', object: 'her', possessive: 'her' } },
  profile: 'laptop (mock)',
  default_bot: 'main',
  voice: { stt: params.get('voice') !== '0', tts: params.get('voice') !== '0' },
};

export const models = [
  { name: 'qwen3:14b', size_gb: 9.3 },
  { name: 'gemma3:12b', size_gb: 8.1 },
  { name: 'llama3.2:3b', size_gb: 2.0 },
];

export const bots = [
  { id: 'main', name: health.assistant.name, model: 'qwen3:14b', system_prompt: 'The main house assistant.', connectors: ['home_assistant'], builtin: true },
  { id: 'b-recipes', name: 'Recipe helper', model: 'gemma3:12b', system_prompt: 'Suggest simple vegetarian dinners from what is in the kitchen. Keep steps short.', connectors: [], builtin: false },
  { id: 'b-mail', name: 'Mail drafter', model: 'qwen3:14b', system_prompt: 'Draft polite, short replies. Never invent facts. Drafts only.', connectors: ['gmail'], builtin: false },
];

export const connectors = [
  { id: 'home_assistant', kind: 'home_assistant', name: 'Home Assistant', enabled: true, status: 'ok', detail: 'Connected. 14 devices are exposed to the assistant.', tools: ['list_devices', 'get_state', 'turn_on', 'turn_off', 'activate_scene'], settings: { url: 'http://homeassistant.local:8123' }, secret_env: ['HA_TOKEN'] },
  { id: 'gmail', kind: 'gmail', name: 'Gmail', enabled: true, status: 'needs_setup', detail: 'No sign-in found yet.', tools: ['search', 'read', 'create_draft'], settings: {}, secret_env: ['GMAIL_CLIENT_SECRET_FILE'] },
  { id: 'calendar', kind: 'calendar', name: 'Calendar', enabled: false, status: 'off', detail: 'Read-only. Switched off.', tools: ['list_events'], settings: {}, secret_env: [] },
  { id: 'mcp-notes', kind: 'mcp', name: 'Notes folder', enabled: true, status: 'error', detail: 'The server did not start: command "notes-mcp" was not found.', tools: [], settings: { command: 'notes-mcp --dir ./notes' }, secret_env: [] },
];

// The scripted conversation. Each turn: what was heard, an optional tool, the answer.
export const script = [
  { heard: 'Turn on the living room lamp', tool: { name: 'turn_on', ok: true }, reply: 'Done. The living room lamp is on at forty percent.' },
  { heard: 'दिवाणखान्यातला दिवा बंद कर', tool: { name: 'turn_off', ok: true }, reply: 'ठीक आहे, दिवाणखान्यातला दिवा बंद केला.' },
  { heard: 'Is the front door locked?', tool: { name: 'get_state', ok: true }, reply: 'Yes. The front door has been locked since nine this evening.' },
  { heard: 'What is on the calendar tomorrow?', tool: null, reply: 'The calendar connector is switched off, so I can’t see tomorrow yet. You can turn it on in Settings.' },
  { heard: 'Set the bedroom to movie mode', tool: { name: 'activate_scene', ok: false }, reply: 'I couldn’t do that. There is no scene called movie mode in the bedroom. The scenes I can see are Reading and Night.' },
];
