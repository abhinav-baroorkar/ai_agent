// Small versions of the being of light, one per bot, in the bot's own colour.
// They are the same particle sphere as the big one with far fewer points, and
// they all share one slow drawing loop that runs only while some are on screen.
import { reducedMotion } from '../dom.js';
import { Sphere } from './sphere.js';
import { styleOf } from './styles.js';

const FRAME_MS = 80; // about 12 pictures a second is plenty at this size
const FORGET_MS = 5000;
const WEIGHTS = (state) => ({ idle: 0, listening: 0, thinking: 0, speaking: 0, [state]: 1 });
// the state colours of the main orb; a bot's own colour is tinted a third of the way toward them
const TINT = { listening: [114, 214, 196], thinking: [179, 166, 245] };
const orbs = new Set();
let timer = null;
let last = 0;

function draw(orb, dt, t) {
  const { canvas, sphere, state } = orb;
  const tint = TINT[state];
  const color = tint ? orb.color.map((v, i) => v + (tint[i] - v) * 0.35) : orb.color;
  // a pretend voice for the preview, so listening and speaking move as they would
  const lvl = state === 'speaking' ? Math.abs(Math.sin(t * 9)) * (0.5 + 0.5 * Math.sin(t * 2.3)) : state === 'listening' ? 0.4 + 0.35 * Math.sin(t * 7) : 0;
  const ctx = canvas.getContext('2d');
  const half = canvas.width / 2;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.translate(half, half);
  sphere.draw(ctx, (half * 0.66) / (1 + 0.6 * orb.style.reach), { w: orb.w, lvl, trail: lvl * 0.6, t: t + orb.phase, dt, color }, { halo: 0.7, style: orb.style });
  orb.drawn = true;
}

function tick() {
  const now = performance.now();
  const calm = reducedMotion();
  const dt = calm ? 0 : Math.min(0.2, (now - last) / 1000);
  last = now;
  let shown = 0;
  for (const orb of orbs) {
    if (!orb.canvas.isConnected) {
      // gone from the page, or made and never put on it: forget it
      if (orb.seen || now - orb.born > FORGET_MS) orbs.delete(orb);
      continue;
    }
    orb.seen = true;
    if (orb.canvas.offsetParent === null || document.hidden) continue;
    shown += 1;
    if (!calm || !orb.drawn) draw(orb, dt, now / 1000);
  }
  if (!shown) stop(); // nothing visible: stop until woken (wakeOrbs, a route change, the window returning)
}

function stop() {
  clearInterval(timer);
  timer = null;
}

export function wakeOrbs() {
  wake();
}

function wake() {
  if (timer || !orbs.size) return;
  last = performance.now();
  timer = setInterval(tick, FRAME_MS);
}

document.addEventListener('visibilitychange', wake);
window.addEventListener('hashchange', () => setTimeout(wake, 0));

// A canvas showing a small orb. color is [r, g, b]; size is its width in CSS pixels;
// style is a breathing style id (styles.js); state is idle, listening, thinking or speaking.
export function miniOrb(color, size = 40, { style = 'glow', state = 'idle' } = {}) {
  const canvas = document.createElement('canvas');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = canvas.height = Math.round(size * dpr);
  canvas.style.width = canvas.style.height = `${size}px`;
  canvas.className = 'mini-orb';
  canvas.setAttribute('aria-hidden', 'true');
  const points = Math.round(Math.min(1100, 110 + size * 5));
  orbs.add({ canvas, color, sphere: new Sphere(points, size < 60 ? 1 : 0.9), phase: Math.random() * 20, seen: false, drawn: false, born: performance.now(),
    style: styleOf(style), state, w: WEIGHTS(state) });
  wake();
  return canvas;
}

// Change what an orb shows: its breathing style, or its state (for a preview, or a chat).
export function setOrb(canvas, { style, state }) {
  for (const orb of orbs) {
    if (orb.canvas !== canvas) continue;
    if (style) orb.style = styleOf(style);
    if (state) { orb.state = state; orb.w = WEIGHTS(state); }
    orb.drawn = false;
  }
  wake();
}
