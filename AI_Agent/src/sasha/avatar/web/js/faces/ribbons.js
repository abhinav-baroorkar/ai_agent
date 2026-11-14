// The flair around the being: flat painted ribbons with a pale crest line,
// in the manner of old woodblock prints of waves, wind and fire. Each state
// has its own element, and every change of state throws one brush sweep.
//   listening: water that curls in toward the being, drawing the voice in
//   thinking:  wind streaks that circle it
//   speaking:  flames that rise and reach out with the loudness
//   idle:      a faint breath, almost still
// Everything is a handful of polygons per frame; no particles.
import { COLORS, rgba, mix, rand } from './stage.js';
import { WHITE } from './parts.js';

const STEPS = 22;
// Flame tongues: angle from straight up (radians) and relative height. Deliberately uneven.
const TONGUES = [[-1.95, 0.42], [-1.3, 0.72], [-0.92, 0.5], [-0.5, 0.98], [-0.1, 1.2], [0.33, 0.8], [0.72, 0.56], [1.22, 0.78], [1.8, 0.38]];
// Flat print colours, a little deeper than the light of the being itself.
const INK = { idle: [236, 170, 96], listening: [72, 190, 176], thinking: [150, 136, 236], speaking: [246, 158, 66] };
const TAU = Math.PI * 2;

// Fills a ribbon along `pts` ([x, y] pairs) whose half-width at s (0..1) is wid(s),
// then draws the pale crest along one edge and a thin dark line along the other.
function band(ctx, pts, wid, color, alpha) {
  if (alpha < 0.01) return;
  const n = pts.length;
  const left = [];
  const right = [];
  for (let i = 0; i < n; i++) {
    const a = pts[Math.max(0, i - 1)];
    const b = pts[Math.min(n - 1, i + 1)];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const nx = -(b[1] - a[1]) / len;
    const ny = (b[0] - a[0]) / len;
    const h = wid(i / (n - 1));
    left.push([pts[i][0] + nx * h, pts[i][1] + ny * h]);
    right.push([pts[i][0] - nx * h, pts[i][1] - ny * h]);
  }
  const trace = (list, start) => list.forEach((p, i) => (i || !start ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.beginPath();
  trace(left, true);
  trace(right.reverse(), false);
  ctx.closePath();
  ctx.fillStyle = rgba(color, alpha * 0.72);
  ctx.fill();
  ctx.lineJoin = ctx.lineCap = 'round';
  ctx.beginPath();
  trace(left, true);
  ctx.strokeStyle = rgba(mix(color, WHITE, 0.75), alpha);
  ctx.lineWidth = 0.014;
  ctx.stroke();
  ctx.beginPath();
  trace(right, true);
  ctx.strokeStyle = rgba(mix(color, [12, 16, 28], 0.55), alpha * 0.7);
  ctx.lineWidth = 0.008;
  ctx.stroke();
}

// Adds a tight curl to the end of a path, like the crest of a printed wave.
function curl(pts, turn, size) {
  const [a, b] = [pts[pts.length - 2], pts[pts.length - 1]];
  let ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
  let [x, y] = b;
  for (let i = 0; i < 9; i++) {
    ang += turn * (0.5 + i * 0.06);
    const step = size * (1 - i / 11);
    x += Math.cos(ang) * step;
    y += Math.sin(ang) * step;
    pts.push([x, y]);
  }
  return pts;
}

const taper = (s) => Math.sin(Math.PI * s) ** 0.7;

export class Ribbons {
  constructor() {
    this.sweeps = [];
  }

  // One broad stroke around the being when the state changes.
  sweep(state) {
    if (state === 'speaking') return; // the flames rising are the flourish there
    this.sweeps.push({ age: 0, from: rand(0, TAU), dir: Math.random() < 0.5 ? -1 : 1, color: INK[state] || COLORS.idle });
    if (this.sweeps.length > 3) this.sweeps.shift();
  }

  // ctx is scaled so the being's radius is 1, centred on it.
  draw(ctx, mood) {
    const { w, t, lvl, trail, calm } = mood;
    if (w.listening > 0.01) this.water(ctx, t, w.listening, 0.75 + 0.5 * lvl, INK.listening);
    if (w.thinking > 0.01) this.wind(ctx, t, w.thinking, INK.thinking, 4);
    if (w.speaking > 0.01) this.flame(ctx, t, w.speaking, calm ? 0.35 : trail, lvl, INK.speaking, calm ? 0 : -(mood.x ? mood.x.vel : 0) * 0.3);
    if (w.idle > 0.01) this.wind(ctx, t * 0.14, w.idle * 0.16, INK.idle, 2);
    if (calm) return;
    for (const s of this.sweeps) {
      s.age += mood.dt / 0.95;
      if (s.age >= 1) continue;
      const ease = 1 - (1 - s.age) ** 3;
      const head = s.from + s.dir * ease * 5.4;
      const span = 2.4 * Math.sin(Math.PI * Math.min(1, s.age * 1.15));
      const pts = [];
      for (let i = 0; i <= STEPS; i++) {
        const a = head - s.dir * span * (1 - i / STEPS);
        const r = 1.42 + 0.1 * Math.sin(a * 2 + s.from);
        pts.push([Math.cos(a) * r, Math.sin(a) * r * 0.92]);
      }
      curl(pts, -s.dir, 0.07);
      band(ctx, pts, (q) => 0.07 * Math.sin(Math.PI * Math.min(1, q * 1.25)) ** 0.5, s.color, 0.85 * (1 - s.age) ** 0.6);
    }
  }

  // Three arms spiral inward, each ending in a curled crest.
  water(ctx, t, weight, swell, color) {
    for (let j = 0; j < 3; j++) {
      const p = (t * 0.3 + j / 3) % 1; // how far this arm has travelled inward
      const base = (j * TAU) / 3 + t * 0.22;
      const pts = [];
      for (let i = 0; i <= STEPS; i++) {
        const q = Math.max(0, p - 0.5 * (1 - i / STEPS));
        const r = 1.72 - 0.62 * q;
        pts.push([Math.cos(base + q * 3.3) * r, Math.sin(base + q * 3.3) * r * 0.92]);
      }
      curl(pts, 1, 0.06);
      const fade = Math.sin(Math.PI * p) ** 0.6;
      band(ctx, pts, (s) => 0.07 * swell * taper(Math.min(1, s * 1.2)), color, weight * fade);
      // a thinner companion line just outside, as printed waves are doubled
      band(ctx, pts.slice(0, STEPS).map(([x, y]) => [x * 1.09, y * 1.09]), (s) => 0.022 * taper(s), color, weight * fade * 0.7);
    }
  }

  // Streaks that circle the being on tilted paths, thick at the head, thin at the tail.
  wind(ctx, t, weight, color, count) {
    for (let j = 0; j < count; j++) {
      const dir = j % 2 ? -1 : 1;
      const head = dir * t * (2.1 + 0.35 * j) + j * 1.7;
      const tilt = (j % 2 ? -0.42 : 0.38) + 0.1 * j;
      const [rx, ry] = [1.45 + 0.13 * j, 0.5 + 0.12 * j];
      const ct = Math.cos(tilt);
      const st = Math.sin(tilt);
      const pts = [];
      for (let i = 0; i <= STEPS; i++) {
        const a = head - dir * 1.9 * (1 - i / STEPS);
        const [x, y] = [Math.cos(a) * rx, Math.sin(a) * ry];
        pts.push([x * ct - y * st, x * st + y * ct]);
      }
      if (j < 2) curl(pts, dir, 0.05);
      band(ctx, pts, (s) => 0.04 * s ** 0.8 * (1 - s) ** 0.12, color, weight * 0.9);
    }
  }

  // Fire around the being: tongues that start hidden behind its body, all
  // rising, of uneven height and spacing, so the mass reads as one flame with
  // the being inside it. They lengthen with the voice and lean against its motion.
  flame(ctx, t, weight, reach, flick, color, lean) {
    for (const [j, [theta, tall]] of TONGUES.entries()) {
      const [rx, ry] = [Math.sin(theta), -Math.cos(theta)]; // outward from the centre at this tongue
      const up = 1 / Math.hypot(rx * 0.55, ry * 0.55 - 1);
      const [dx, dy] = [rx * 0.55 * up, (ry * 0.55 - 1) * up];
      const len = tall * (0.3 + 0.8 * reach + 0.09 * Math.sin(t * 3.3 + j * 2.1));
      const pts = [];
      for (let i = 0; i <= STEPS; i++) {
        const s = i / STEPS;
        const sway = Math.sin(s * 3.6 - t * 5.2 + j * 1.9) * 0.1 * s * (0.6 + flick);
        // side tongues start outward and then bend up; all of them lean with the motion
        pts.push([rx * 0.66 + dx * (len + 0.12) * s + sway + lean * s * s, ry * 0.66 + dy * (len + 0.12) * s - Math.abs(rx) * 0.35 * len * s * s]);
      }
      const body = (s) => (1 - s) ** 0.9;
      band(ctx, pts, (s) => 0.21 * body(s), color, weight);
      band(ctx, pts.slice(2, 14), (s) => 0.06 * body(s), mix(color, WHITE, 0.3), weight * 0.5);
    }
  }
}
