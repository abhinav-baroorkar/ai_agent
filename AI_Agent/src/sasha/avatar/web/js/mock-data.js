// Made-up data for ?mock=1. Nothing here is real or taken from anyone's home:
// rooms, devices, bots and model names are generic invented examples. The
// shapes match what the real server returns (api-contract v0.1.1).
const params = new URLSearchParams(location.search);

export const health = {
  ok: true,
  assistant: { name: params.get('name') || 'Assistant', pronouns: { subject: 'she', object: 'her', possessive: 'her' } },
  profile: 'laptop (mock)',
  default_bot: 'main',
  voice: { stt: params.get('voice') !== '0', tts: params.get('voice') !== '0', tts_languages: ['en'] },
  limits: { max_sockets: 4, reserved_sockets: 1, browser_sockets: 1 },
};

export const models = [
  { name: 'example-chat:12b', size_gb: 7.6, tools: true },
  { name: 'example-large:20b', size_gb: 13.8, tools: true },
  { name: 'example-small:3b', size_gb: 2.0, tools: false },
];

export const bots = [
  { id: 'main', name: health.assistant.name, model: 'example-chat:12b', system_prompt: 'The main house assistant.', connectors: ['home_assistant'], style: 'glow', builtin: true },
  // the server's own wake-word bot: built in, named after the main assistant
  { id: 'voice', name: `${health.assistant.name} (wake word)`, model: 'example-chat:12b', system_prompt: 'The main house assistant.', connectors: ['home_assistant'], builtin: true },
  { id: 'b-web', name: 'Web researcher', model: 'example-chat:12b', system_prompt: 'Look things up and say where you found them.', connectors: ['browser'], style: 'gale', builtin: false },
  { id: 'b-recipes', name: 'Recipe helper', model: 'example-large:20b', system_prompt: 'Suggest simple dinners from a list of ingredients. Keep steps short.', connectors: [], style: 'bloom', builtin: false },
  { id: 'b-mail', name: 'Mail drafter', model: 'example-chat:12b', system_prompt: 'Draft polite, short replies. Never invent facts. Drafts only.', connectors: ['gmail'], style: 'ember', builtin: false },
];

// Same fields the real server returns. Addresses and tokens are secrets, so they never appear in settings.
export const connectors = [
  { id: 'home_assistant', kind: 'home_assistant', name: 'Home Assistant', enabled: true, status: 'ok', detail: 'Connected. 14 devices are exposed to the assistant.', tools: ['list_devices', 'get_state', 'call_service'], settings: { timeout_s: 10, exposed_entities: [] }, secret_env: ['HA_URL', 'HA_TOKEN'] },
  { id: 'browser', kind: 'browser', name: 'Browser', enabled: true, status: 'ok', detail: 'Uses its own private Chrome profile, read-only for now', tools: ['search', 'open_url', 'read_page', 'click', 'scroll', 'back'], settings: { search_template: '' }, secret_env: [] },
  { id: 'gmail', kind: 'gmail', name: 'Gmail', enabled: true, status: 'needs_setup', detail: 'No sign-in found yet.', tools: ['search_mail', 'read_mail', 'create_draft'], settings: {}, secret_env: ['GMAIL_CLIENT_SECRET_FILE'] },
  { id: 'calendar', kind: 'calendar', name: 'Calendar', enabled: false, status: 'off', detail: 'Read-only. Switched off.', tools: ['list_events'], settings: {}, secret_env: [] },
  { id: 'mcp-notes', kind: 'mcp', name: 'Notes folder', enabled: true, status: 'error', detail: 'The server did not start: command "notes-mcp" was not found.', tools: [], settings: { command: 'notes-mcp --dir ./notes' }, secret_env: [] },
];

// The scripted conversation. Each turn: what was heard, an optional tool, the answer.
export const script = [
  { keys: ['on', 'lamp'], heard: 'Turn on the living room lamp', tool: { name: 'call_service', ok: true }, reply: 'Done. The living room lamp is on at forty percent.' },
  { keys: ['बंद'], heard: 'दिवाणखान्यातला दिवा बंद कर', tool: { name: 'call_service', ok: true }, reply: 'ठीक आहे, दिवाणखान्यातला दिवा बंद केला.' },
  { keys: ['door'], heard: 'Is the front door locked?', tool: { name: 'get_state', ok: true }, reply: 'Yes. The front door has been locked since nine this evening.' },
  { keys: ['calendar'], heard: 'What is on the calendar tomorrow?', tool: null, reply: 'The calendar connector is switched off, so I can’t see tomorrow yet. You can turn it on in Settings.' },
  // An answer with the simple formatting models like to use, to check how chats render it.
  { keys: ['dinner'], heard: 'Suggest a quick dinner', tool: null, reply: 'Here is a **quick dinner** that takes about 20 minutes:\n\n## Lemon chickpea rice\n\n1. Rinse *one cup* of rice and start it cooking.\n2. Fry an onion, then add a tin of chickpeas.\n3. Stir in lemon juice and a handful of herbs.\n\nYou will need:\n\n- rice and chickpeas\n- an onion and a lemon\n- herbs (`coriander` or `parsley`)\n\n```\nrice 1 cup : water 2 cups\n```\n\nSee [a longer version](example.org/recipes) if you have more time. <b>Not bold</b>, just text.' },
  { keys: ['movie'], heard: 'Set the bedroom to movie mode', tool: { name: 'call_service', ok: false }, reply: 'I couldn’t do that. There is no scene called movie mode in the bedroom. The scenes I can see are Reading and Night.' },
];
