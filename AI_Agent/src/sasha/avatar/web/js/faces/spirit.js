// The being of light. sphere.js draws its body and ribbons.js its flair;
// this module is its behaviour: how it floats, wanders, leans in, hops,
// settles, and what its small companion motes do. With `eyes` it also gets
// two eyes of light.
import { Stage, Blinker, mix, rand, clamp } from './stage.js';
import { WHITE, glow, lightEye, Spring } from './parts.js';
import { Sphere } from './sphere.js';
import { Ribbons } from './ribbons.js';

const SHADOW = [8, 10, 18];

class Spirit extends Stage {
  constructor(root, { eyes }) {
    super(root, 2.1);
    this.eyes = eyes;
    this.sphere = new Sphere();
    this.ribbons = new Ribbons();
    this.blinker = new Blinker();
    this.x = new Spring(0, 16, 5);
    this.y = new Spring(0, 16, 5);
    this.size = new Spring(1, 70, 9);
    this.goal = { x: 0, y: 0, until: 0, next: performance.now() + rand(5000, 9000) };
    this.shimmer = { p: -1, next: performance.now() + rand(7000, 12000) };
    this.glee = 0; // a moment of being pleased, right after it has spoken
    this.burst = 0; // companions scattering when it "gets" something
    this.orbit = 0;
    this.motes = Array.from({ length: 8 }, (_, i) => ({
      a: (i / 8) * 6.283 + rand(0, 0.5), r: rand(1.3, 1.75), tilt: rand(-0.5, 0.5), sp: rand(0.35, 0.7) * (i % 2 ? 1 : -1),
      ph: rand(0, 6.28), size: rand(0.6, 1.1),
    }));
  }

  setState(state) {
    const from = this.state;
    super.setState(state);
    if (this.state === from) return;
    this.ribbons.sweep(this.state);
    if (this.calm) return;
    if (this.state === 'listening') this.size.vel += 0.9; // perks up
    if (this.state === 'thinking') this.size.vel -= 0.7; // draws inward
    if (this.state === 'speaking') { // has the answer: a small hop, companions scatter
      this.y.vel -= 0.9;
      this.size.vel += 1.1;
      this.burst = 1;
    }
    if (this.state === 'idle' && from === 'speaking') { // settles, pleased
      this.y.vel += 0.6;
      this.size.vel -= 0.9;
      this.glee = 1;
    }
  }

  draw(ctx, u) {
    const { w, lvl, t, dt, color } = this;
    const now = performance.now();
    const R0 = u * 0.56;

    // idle surprises: now and then it drifts off to look at something, or a shimmer crosses it
    if (w.idle > 0.9 && !this.calm) {
      if (now > this.goal.next) {
        this.goal = { x: rand(-0.42, 0.42), y: rand(-0.16, 0.12), until: now + rand(1800, 3200), next: now + rand(9000, 17000) };
      }
      if (now > this.shimmer.next) this.shimmer = { p: 0, next: now + rand(9000, 18000) };
    }
    const away = now < this.goal.until && w.idle > 0.5;
    if (this.shimmer.p >= 0) this.shimmer.p = this.shimmer.p + dt / 1.4 > 1 ? -1 : this.shimmer.p + dt / 1.4;
    this.glee = Math.max(0, this.glee - dt / 1.6);
    this.burst = Math.max(0, this.burst - dt / 0.9);

    // float: a slow bob that goes still while it listens
    const still = 1 - 0.8 * w.listening;
    const px = this.x.step(away ? this.goal.x : 0, dt) + Math.sin(t * 0.43) * 0.04 * still;
    const py = this.y.step(away ? this.goal.y : 0.04 * w.thinking, dt) + Math.sin(t * 0.9) * 0.035 * still - 0.05 * this.glee * Math.abs(Math.sin(t * 9));
    const scale = this.size.step(1 + 0.08 * w.listening - 0.06 * w.thinking + 0.05 * lvl * w.speaking, dt);
    const R = R0 * scale;
    const speed = Math.hypot(this.x.vel, this.y.vel);

    // light on the floor stays put while the being moves above it
    ctx.save();
    ctx.translate(px * R0 * 0.6, R0 * 1.42);
    ctx.scale(1, 0.12);
    glow(ctx, 0, 0, R0 * (1.15 - 0.3 * py), color, 0.24 + 0.25 * lvl * w.speaking);
    ctx.restore();

    ctx.translate(px * R0, (py - 0.1) * R0);
    // a fading trail behind it when it moves
    for (let i = 1; i <= 4 && speed > 0.05; i++) {
      glow(ctx, -this.x.vel * R0 * 0.09 * i, -this.y.vel * R0 * 0.09 * i, R * (0.95 - 0.12 * i), color, Math.min(0.2, speed * 0.25) / i);
    }

    ctx.save();
    ctx.scale(R, R);
    this.companions(ctx, false);
    this.ribbons.draw(ctx, this);
    ctx.restore();

    // stretch a little along the direction of travel
    ctx.save();
    const dir = Math.atan2(this.y.vel, this.x.vel);
    ctx.rotate(dir);
    ctx.scale(1 + Math.min(0.12, speed * 0.1), 1 - Math.min(0.08, speed * 0.07));
    ctx.rotate(-dir);
    this.sphere.draw(ctx, R, this, { shimmer: this.shimmer.p, core: this.eyes ? 0.55 : 1 });
    ctx.restore();

    ctx.save();
    ctx.scale(R, R);
    if (this.eyes) this.face(ctx, R, now, away);
    this.companions(ctx, true);
    ctx.restore();
  }

  // Eight small motes keep it company: they come close to listen, race in a
  // ring while it thinks, scatter when it has the answer and dance with the voice.
  companions(ctx, front) {
    const { w, lvl, t, color } = this;
    if (!front) this.orbit += this.dt * (1 + 3.5 * w.thinking + 2.5 * this.glee);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (const m of this.motes) {
      const a = m.a + this.orbit * m.sp;
      const z = Math.sin(a);
      if (z > 0 !== front) continue;
      const r = m.r * (1 - 0.26 * w.listening - 0.12 * w.thinking) + 0.7 * this.burst * Math.sin(Math.PI * this.burst) +
        0.16 * lvl * w.speaking * Math.sin(t * 9 + m.ph);
      const tilt = m.tilt * (1 - 0.85 * w.thinking);
      const x = Math.cos(a) * r;
      const y = z * r * 0.32 + Math.cos(a) * r * tilt + Math.sin(t * 0.8 + m.ph) * 0.06 - 0.25 * this.glee * Math.sin(a * 2 + t * 6) * 0.5;
      const lit = 0.45 + 0.55 * (0.5 + 0.5 * Math.sin(t * 1.4 + m.ph)) + lvl * w.speaking;
      const s = 0.03 * m.size * (1 + 0.3 * z);
      glow(ctx, x, y, s * 4.5, color, 0.28 * lit);
      glow(ctx, x, y, s * 1.3, mix(color, WHITE, 0.7), 0.95 * lit);
    }
    ctx.restore();
  }

  // Two eyes of light inside the glow: soft ovals at rest, wide and a little
  // uneven when curious, narrowed and looking up when focused, crescents when pleased.
  face(ctx, R, now, away) {
    const { w, lvl, color } = this;
    glow(ctx, 0, -0.04, 0.62, SHADOW, 0.5); // a pocket of shade so the eyes read against the core
    const pleased = clamp(w.speaking + this.glee);
    const calmW = Math.max(0, w.idle - this.glee);
    const blink = 1 - 0.94 * this.blinker.value(now, this.calm);
    const rx = 0.072 * calmW + 0.086 * w.listening + 0.082 * w.thinking + 0.09 * pleased;
    const ry = (0.118 * calmW + 0.15 * w.listening + 0.056 * w.thinking + 0.13 * pleased) * blink;
    const lower = calmW + w.listening + 0.8 * w.thinking - 0.5 * pleased;
    const lookX = clamp((away ? this.goal.x - this.x.v : 0) * 0.35, -0.09, 0.09) + 0.06 * w.thinking;
    const lookY = -0.06 - 0.03 * w.listening - 0.08 * w.thinking - 0.02 * lvl * w.speaking;
    ctx.save();
    ctx.rotate(-0.07 * w.listening + 0.05 * w.thinking);
    for (const side of [-1, 1]) {
      const k = 1 + 0.08 * side * w.listening;
      lightEye(ctx, R, { x: side * 0.21 + lookX, y: lookY, rx: rx * k, ry: ry * k, lower, color, bright: lvl });
    }
    ctx.restore();
  }
}

export function create(root, options) {
  return new Spirit(root, options);
}
