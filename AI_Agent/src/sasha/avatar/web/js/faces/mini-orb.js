// Small versions of the being of light, one per bot, in the bot's own colour.
// They are the same particle sphere as the big one with far fewer points, and
// they all share one slow drawing loop that runs only while some are on screen.
import { reducedMotion } from '../dom.js';
import { Sphere } from './sphere.js';

const FRAME_MS = 80; // about 12 pictures a second is plenty at this size
const FORGET_MS = 5000;
const IDLE = { idle: 1, listening: 0, thinking: 0, speaking: 0 };
const orbs = new Set();
let timer = null;
let last = 0;

function draw(orb, dt, t) {
  const { canvas, sphere, color } = orb;
  const ctx = canvas.getContext('2d');
  const half = canvas.width / 2;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.translate(half, half);
  sphere.draw(ctx, half * 0.66, { w: IDLE, lvl: 0, trail: 0, t: t + orb.phase, dt, color }, { halo: 0.7 });
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

// A canvas showing a small orb. color is [r, g, b]; size is its width in CSS pixels.
export function miniOrb(color, size = 40) {
  const canvas = document.createElement('canvas');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = canvas.height = Math.round(size * dpr);
  canvas.style.width = canvas.style.height = `${size}px`;
  canvas.className = 'mini-orb';
  canvas.setAttribute('aria-hidden', 'true');
  const points = Math.round(Math.min(1100, 110 + size * 5));
  orbs.add({ canvas, color, sphere: new Sphere(points, size < 60 ? 1 : 0.9), phase: Math.random() * 20, seen: false, drawn: false, born: performance.now() });
  wake();
  return canvas;
}
