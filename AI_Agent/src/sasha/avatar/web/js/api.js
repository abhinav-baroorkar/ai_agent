// HTTP + WebSocket access. With ?mock=1 everything is answered in the browser
// by mock.js, so the UI runs with no brain server at all.
const params = new URLSearchParams(location.search);
export const MOCK = params.has('mock') && params.get('mock') !== '0';
const mock = MOCK ? await import('./mock.js') : null;

const TIMEOUT_MS = 8000;

// The brain's per-launch token (take-over design 9.1, C4). `sasha window` opens the
// window at #t=<token>; it is kept in memory only and stripped from the address at
// once, so it stays out of the history and out of a copied or reloaded URL.
const TOKEN = (() => {
  const t = new URLSearchParams(location.hash.slice(1)).get('t') || '';
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  return /^[0-9a-f]{64}$/.test(t) ? t : '';
})();
export const HAS_TOKEN = TOKEN !== '';

export async function api(method, path, body) {
  if (mock) return mock.mockFetch(method, path, body);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(path, {
      method,
      signal: ctl.signal,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(TOKEN ? { 'X-Window-Token': TOKEN } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `The server answered ${res.status}.`);
    return data;
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('The server took too long to answer.');
    if (err instanceof TypeError) throw new Error('Can’t reach the server.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// The live view of the assistant's browser has its own socket and its own slot.
export function openBrowserSocket() {
  if (mock) return new mock.MockBrowserSocket();
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const protocols = TOKEN ? ['brain.v1', `token.${TOKEN}`] : ['brain.v1'];
  return new WebSocket(`${scheme}://${location.host}/ws/browser`, protocols);
}

// speak: ask the server to also send speech for this conversation (Home only).
export function openSocket(botId, speak = false) {
  if (mock) return new mock.MockSocket(botId, speak);
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${scheme}://${location.host}/ws?bot=${encodeURIComponent(botId)}${speak ? '&speak=1' : ''}`);
  ws.binaryType = 'arraybuffer';
  return ws;
}
