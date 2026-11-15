// The being's breath: fine points of light and soft mist that flow around the
// sphere, in the same visual language as the body so it reads as one being.
//   idle:      a thin veil drifts out and is drawn back in with its breathing
//   listening: particles stream in from all around and spiral into the iris
//   thinking:  currents of mist circle it
//   speaking:  embers rise and curl off it with the loudness
// A change of state sends a soft swirl through all of it. Under reduced
// motion nothing flows: what is there stays as a still, faint halo.
import { rgba, mix, rand, clamp } from './stage.js';
import { WHITE } from './parts.js';

const N = 520;
const TAU = Math.PI * 2;
const LEVELS = 4; // brightness groups, so each frame needs only a few draw calls
const surface = (h) => 1.1 * Math.sqrt(Math.max(0.04, 1 - (h / 1.3) ** 2));

export class Mist {
  constructor() {
    this.surge = 0;
    this.list = Array.from({ length: N }, (_, i) => {
      const p = { lane: rand(-1, 1), size: 0.5 + Math.random() * Math.random() * 1.7, puff: i % 7 === 0, x: 0, y: 0, z: 0, px: null, py: 0, sx: 0, sy: 0, alpha: 0 };
      this.spawn(p, 'idle');
      p.age = rand(0, p.life); // start mid-life so the veil is there from the first frame
      return p;
    });
    this.puff = document.createElement('canvas');
    this.puff.width = this.puff.height = 64;
    this.puffColor = '';
  }

  spawn(p, state) {
    p.a = rand(0, TAU);
    p.age = 0;
    p.life = rand(3, 6.5);
    p.px = null;
    if (state === 'listening') {
      p.r = rand(1.7, 2.35);
      p.h = rand(-1.3, 1.3);
    } else if (state === 'speaking') {
      p.h = rand(-0.7, 0.75);
      p.r = surface(p.h) * rand(0.9, 1.08);
      p.life = rand(1.3, 2.8);
    } else if (state === 'thinking') {
      p.r = rand(1.2, 1.75);
      p.h = p.lane * 0.4;
    } else {
      p.h = rand(-1.05, 1.05);
      p.r = surface(p.h) + rand(0, 0.22);
    }
  }

  kick() {
    this.surge = 1;
  }

  step(mood) {
    const { w, t, lvl, state } = mood;
    const dt = mood.calm ? 0 : mood.dt;
    this.surge = Math.max(0, this.surge - dt / 0.9);
    const breath = Math.sin(t * 0.9);
    const rise = 0.35 + 1.6 * lvl;
    const swirl = this.surge * Math.sin(Math.PI * this.surge);
    for (const p of this.list) {
      const turn = p.lane > 0 ? 1 : -1;
      const near = Math.max(0.3, p.r);
      // each state pulls the particle its own way; the weights blend them
      const dr =
        w.idle * (surface(p.h) + 0.1 + 0.15 * breath - p.r) * 1.3 +
        w.listening * -(0.75 + 0.5 * lvl) * p.r +
        w.thinking * (1.3 + 0.3 * Math.abs(p.lane) - p.r) * 1.6 +
        w.speaking * (0.1 + 0.3 * lvl + 0.16 * Math.sin(t * 3 + p.a * 3)) +
        swirl * 0.5 * (1 - w.speaking);
      const da =
        w.idle * 0.16 * turn + (w.listening * 1.2) / near + w.thinking * 1.5 * turn + w.speaking * (0.22 * p.lane + 0.6 * Math.sin(t * 2.2 + p.h * 2.5 + p.lane * 6)) + swirl * 3.2 * turn * (1 - w.speaking);
      const dh =
        w.idle * Math.sin(t * 0.5 + p.a) * 0.05 +
        w.listening * -p.h * 1.2 +
        w.thinking * (p.lane * 0.42 + 0.13 * Math.sin(p.a * 2 + t) - p.h) * 2 -
        w.speaking * rise * (0.55 + 0.45 * p.size);
      p.r += dr * dt;
      p.a += da * dt;
      p.h += dh * dt;
      p.age += dt * (1 + 0.5 * w.speaking);
      if (p.age > p.life || p.r < 0.4 || p.h < -2.4 || p.r > 2.7) this.spawn(p, state);

      const fresh = p.px === null;
      p.px = p.x;
      p.py = p.y;
      p.z = Math.sin(p.a);
      p.x = Math.cos(p.a) * p.r;
      p.y = p.h + p.z * p.r * 0.28;
      // how far it travels in 1/20 s: the length of its streak, whatever the frame rate
      p.sx = fresh || !dt ? 0 : ((p.x - p.px) / dt) * 0.05;
      p.sy = fresh || !dt ? 0 : ((p.y - p.py) / dt) * 0.05;
      let alpha = clamp(p.age / 0.5) * clamp((p.life - p.age) / 0.9);
      alpha *= 1 - w.listening * (1 - clamp((2.4 - p.r) / 0.7) * clamp((p.r - 0.4) / 0.5)); // fades in as it is drawn closer, and out into the iris
      alpha *= 1 - w.speaking * (1 - clamp((p.h + 2.4) / 1.3)); // embers die out as they climb
      p.alpha = alpha * (0.55 + 0.2 * w.listening + 0.1 * w.thinking + (0.05 + 0.6 * lvl) * w.speaking) * (p.z > 0 ? 1 : 0.5);
    }
  }

  // ctx is scaled so the being's radius is 1. front: the half nearer the viewer.
  draw(ctx, mood, front) {
    const { color } = mood;
    const tint = rgba(color, 1);
    if (tint !== this.puffColor) this.paintPuff(color, tint);
    const lines = Array.from({ length: LEVELS }, () => new Path2D());
    const dots = Array.from({ length: LEVELS }, () => new Path2D());
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (const p of this.list) {
      if (p.z > 0 !== front || p.alpha < 0.02) continue;
      if (p.puff) {
        const s = 0.42 * p.size;
        ctx.globalAlpha = p.alpha * 0.2;
        ctx.drawImage(this.puff, p.x - s, p.y - s, s * 2, s * 2);
        continue;
      }
      const k = Math.min(LEVELS - 1, (p.alpha * LEVELS) | 0);
      const s = 0.0105 * p.size * (1 + 0.25 * p.z);
      dots[k].moveTo(p.x + s, p.y);
      dots[k].arc(p.x, p.y, s, 0, TAU);
      if (Math.abs(p.sx) + Math.abs(p.sy) > 0.012) { // a short streak behind it: motion blur
        lines[k].moveTo(p.x - p.sx, p.y - p.sy);
        lines[k].lineTo(p.x, p.y);
      }
    }
    ctx.globalAlpha = 1;
    ctx.lineCap = 'round';
    ctx.lineWidth = 0.011;
    for (let k = 0; k < LEVELS; k++) {
      const a = (k + 0.7) / LEVELS;
      ctx.strokeStyle = rgba(color, a * 0.38);
      ctx.stroke(lines[k]);
      ctx.fillStyle = rgba(mix(color, WHITE, 0.45), a);
      ctx.fill(dots[k]);
    }
    ctx.restore();
  }

  // The soft blob used for mist, repainted only when the colour changes.
  paintPuff(color, tint) {
    const c = this.puff.getContext('2d');
    c.clearRect(0, 0, 64, 64);
    const g = c.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, rgba(color, 0.55));
    g.addColorStop(0.5, rgba(color, 0.16));
    g.addColorStop(1, rgba(color, 0));
    c.fillStyle = g;
    c.fillRect(0, 0, 64, 64);
    this.puffColor = tint;
  }
}
