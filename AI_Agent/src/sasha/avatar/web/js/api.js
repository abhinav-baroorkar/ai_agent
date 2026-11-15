// HTTP + WebSocket access. With ?mock=1 everything is answered in the browser
// by mock.js, so the UI runs with no brain server at all.
const params = new URLSearchParams(location.search);
export const MOCK = params.has('mock') && params.get('mock') !== '0';
const mock = MOCK ? await import('./mock.js') : null;

const TIMEOUT_MS = 8000;

export async function api(method, path, body) {
  if (mock) return mock.mockFetch(method, path, body);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(path, {
      method,
      signal: ctl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
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

// speak: ask the server to also send speech for this conversation (Home only).
export function openSocket(botId, speak = false) {
  if (mock) return new mock.MockSocket(botId, speak);
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${scheme}://${location.host}/ws?bot=${encodeURIComponent(botId)}${speak ? '&speak=1' : ''}`);
  ws.binaryType = 'arraybuffer';
  return ws;
}
