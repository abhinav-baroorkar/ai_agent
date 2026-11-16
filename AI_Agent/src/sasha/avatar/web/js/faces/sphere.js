// The body of the being: a sphere of about 2,800 points of light with real
// depth. It opens toward the voice when listening, gathers into crossed rings
// when thinking, and ripples with the speech when speaking.
import { rgba, mix } from './stage.js';
import { WHITE, glow } from './parts.js';

const N = 2800;
const BUCKETS = 7;

export class Sphere {
  // count: how many points; dot: how much larger to draw them (small orbs need both changed)
  constructor(count = N, dot = 1) {
    this.dot = dot;
    this.spin = 0;
    this.points = [];
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < count; i++) {
      // evenly spread, then nudged so no lattice pattern shows
      const y = Math.max(-1, Math.min(1, 1 - (2 * (i + 0.5)) / count + (Math.random() - 0.5) * 0.03));
      const r = Math.sqrt(1 - y * y);
      const a = i * golden + (Math.random() - 0.5) * 0.25;
      // one point in six floats inside the shell and turns the other way: that is the depth
      const inner = i % 6 === 5 ? 0.35 + Math.random() * 0.4 : 0;
      this.points.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r, ph: Math.random() * 6.28, size: 0.6 + Math.random() * Math.random() * 1.3, group: i % 3, inner });
    }
    this.paths = Array.from({ length: BUCKETS }, () => []);
  }

  // Draws around the origin. ctx is in real pixels; R is the radius.
  // shimmer: -1 for none, or 0..1 as a band of light crosses from top to bottom.
  // halo: scales the glow around it (a small orb keeps it inside its own canvas).
  draw(ctx, R, mood, { shimmer = -1, halo = 1 } = {}) {
    const { w, lvl, trail, t, color } = mood;
    const voice = lvl * (w.speaking + w.listening);
    this.spin += mood.dt * (0.16 + 1.5 * w.thinking + 0.25 * w.speaking);
    const band = shimmer < 0 ? 9 : 1.3 - 2.6 * shimmer;

    // light around and inside the sphere
    ctx.globalCompositeOperation = 'source-over';
    glow(ctx, 0, 0, R * 2.1 * halo, color, 0.2 + 0.2 * voice);
    const coreR = R * (0.55 + 0.25 * w.listening + 0.3 * trail - 0.15 * w.thinking);
    let g = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR);
    g.addColorStop(0, rgba(mix(color, WHITE, 0.6), 0.5 + 0.4 * voice + 0.2 * w.thinking));
    g.addColorStop(0.35, rgba(color, 0.22 + 0.25 * voice));
    g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g;
    ctx.fillRect(-coreR, -coreR, coreR * 2, coreR * 2);
    // the faint skin of the sphere: brightest at the edge, like a soap bubble
    const skin = Math.max(0, 1 - w.thinking - 0.6 * (w.listening + w.speaking));
    g = ctx.createRadialGradient(0, 0, R * 0.7, 0, 0, R * 1.07);
    g.addColorStop(0, rgba(color, 0));
    g.addColorStop(0.8, rgba(color, 0.1 * skin));
    g.addColorStop(0.93, rgba(mix(color, WHITE, 0.3), 0.17 * skin));
    g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g;
    ctx.fillRect(-R * 1.1, -R * 1.1, R * 2.2, R * 2.2);

    const cs = Math.cos(this.spin);
    const sn = Math.sin(this.spin);
    const ci = Math.cos(-this.spin * 0.7);
    const si = Math.sin(-this.spin * 0.7);
    const tilt = 0.36 + 0.07 * Math.sin(t * 0.31);
    const u = R / 0.7;
    const ct = Math.cos(tilt);
    const st = Math.sin(tilt);
    const open = w.listening * (0.3 + 0.55 * lvl);
    for (const b of this.paths) b.length = 0;

    for (const p of this.points) {
      let { x, y, z } = p;
      // slow surface currents
      let r = 1 + 0.045 * Math.sin(x * 3.1 + t * 0.8 + p.ph) * Math.sin(y * 2.6 - t * 0.6) + 0.02 * Math.sin(z * 5 + t * 1.3);
      // speaking: bands of light travel from pole to pole with the voice
      r += w.speaking * lvl * 0.3 * (0.3 + 0.7 * (0.5 + 0.5 * Math.sin(y * 6.5 - t * 7 + p.ph * 0.15)));
      if (w.thinking > 0.002) {
        // thinking: two crossed rings and a tight core
        const k = w.thinking;
        if (p.group === 0) {
          const len = Math.hypot(x, z) || 1;
          x += ((x / len) * 1.08 - x) * k; z += ((z / len) * 1.08 - z) * k; y += (y * 0.07 - y) * k;
        } else if (p.group === 1) {
          const len = Math.hypot(y, z) || 1;
          y += ((y / len) * 0.86 - y) * k; z += ((z / len) * 0.86 - z) * k; x += (x * 0.07 - x) * k;
        } else {
          r *= 1 - 0.62 * k;
        }
      }
      if (p.inner) r *= p.inner;
      x *= r; y *= r; z *= r;
      // spin about the vertical axis, then tip toward the viewer
      const x1 = p.inner ? x * ci + z * si : x * cs + z * sn;
      const z1 = p.inner ? -x * si + z * ci : -x * sn + z * cs;
      let xv = x1;
      let yv = y * ct - z1 * st;
      const zv = y * st + z1 * ct;
      if (open > 0.002 && zv > 0) {
        // listening: the near side opens like an iris, showing the bright core
        const k = 1 + open * zv * zv * 1.5;
        xv *= k; yv *= k;
      }
      const persp = 1 / (1 - zv * 0.26);
      const d = (zv + 1.25) / 2.5;
      const lit = Math.max(0, 1 - Math.abs(y - band) * 5); // the shimmer band passing this point
      const bucket = Math.min(BUCKETS - 1, Math.max(0, ((d + lit * 0.4) * BUCKETS) | 0));
      this.paths[bucket].push(xv * R * persp, yv * R * persp, p.size * persp * (0.8 + 0.35 * Math.sin(t * 1.7 + p.ph * 5) + lit));
    }

    ctx.globalCompositeOperation = 'lighter';
    const dot = Math.max(0.7, u / 250) * this.dot;
    for (let b = 0; b < BUCKETS; b++) {
      const d = (b + 0.5) / BUCKETS;
      const list = this.paths[b];
      ctx.fillStyle = rgba(mix(color, WHITE, d * d * 0.55), 0.22 + 0.7 * d * d);
      ctx.beginPath();
      const s = dot * (0.55 + 1.0 * d);
      for (let i = 0; i < list.length; i += 3) {
        const rr = s * list[i + 2];
        ctx.moveTo(list[i] + rr, list[i + 1]);
        ctx.arc(list[i], list[i + 1], rr, 0, 6.2832);
      }
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';
  }
}
