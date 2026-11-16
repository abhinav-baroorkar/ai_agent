// How a bot is shown: its colour, a line on what it is for, and what it may
// use, in plain words (the word "connector" is never shown to the owner).
const LAMP = [243, 201, 139]; // the main assistant's own colour
const HUES = [[114, 214, 196], [179, 166, 245], [242, 155, 180], [140, 196, 245], [156, 214, 148], [245, 172, 122]];
const KIND_WORDS = { browser: 'the web', home_assistant: 'your home', gmail: 'your mail', calendar: 'your calendar' };

export function colorOf(bot) {
  if (bot.builtin) return LAMP;
  let sum = 0;
  for (const ch of bot.id) sum = (sum * 31 + ch.charCodeAt(0)) % 9973;
  return HUES[sum % HUES.length];
}

export const cssColor = (c) => `rgb(${c.join(',')})`;

// "what it may use" for one connector, e.g. "the web" or "your home".
export const useWord = (connector) => KIND_WORDS[connector.kind] || connector.name;

// { text: "Can use the web", kind: "web" | "own" | "none" }
export function usesOf(bot, connectors) {
  const mine = connectors.filter((c) => bot.connectors.includes(c.id));
  if (!mine.length) return { text: 'Just talks', kind: 'none' };
  const words = mine.map(useWord);
  const list = words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}` : words[0];
  return { text: `Can use ${list}`, kind: mine.some((c) => c.kind === 'browser') ? 'web' : 'own' };
}

// One short line on what the bot is for: the first sentence of its instructions.
export function purposeOf(bot, assistantName, mainId = 'main') {
  if (bot.builtin && bot.id === mainId) return `${assistantName} is the one you talk to on the first screen. You can type to it here.`;
  if (bot.builtin) return `Answers when you say the wake phrase. It uses what ${assistantName} uses unless you change it here.`;
  const text = bot.system_prompt.replace(/\s+/g, ' ').trim();
  if (!text) return 'No instructions yet.';
  const first = text.split(/(?<=[.!?])\s/)[0];
  if (first.length <= 110) return first;
  const cut = first.slice(0, 108);
  const space = cut.lastIndexOf(' ');
  return `${(space > 60 ? cut.slice(0, space) : cut).replace(/[\s,;:–-]+$/, '')}…`; // at a word, not mid-word
}

// Three ways to start a new bot.
export const STARTS = [
  { id: 'web', use: 'web', title: 'A web researcher', name: 'Web researcher',
    about: 'Finds things on the web and reads pages for you. It gets its own browser that you can watch.',
    prompt: 'Look things up on the web when asked. Say where you found each fact, and say so plainly when you could not find something.' },
  { id: 'write', use: 'none', title: 'A writing helper', name: 'Writing helper',
    about: 'Drafts, shortens and tidies text. It only talks; it can’t look anything up.',
    prompt: 'Help write, shorten and tidy text. Keep the owner’s meaning and tone. Ask before changing any fact.' },
  { id: 'blank', use: 'none', title: 'Start from blank', name: '',
    about: 'Describe the job in your own words in the last step.', prompt: '' },
];
