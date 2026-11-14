// Concept 3: a sphere of dark glass with a light inside and two eyes made of
// light. The eyes change shape with the state (soft when idle, wide and
// curious when listening, narrowed and looking up when thinking, happy
// crescents when speaking) and the glow follows the sound.
import { Stage, Blinker, rgba, mix, rand } from './stage.js';

const WHITE = [255, 252, 244];

class Glass extends Stage {
  constructor(root) {
    super(root, 1.7);
    this.blinker = new Blinker();
    this.look = 0; // sideways glance while idle
    this.lookTo = 0;
    this.lookAt = performance.now() + 5000;
  }

  draw(ctx, u) {
    const { w, lvl, trail, t, color } = this;
    const now = performance.now();
    const R = u * 0.68;
    const voice = lvl * (w.speaking + w.listening);
    const glow = trail * (w.speaking + w.listening);
    const c = (a) => rgba(color, a);

    if (now > this.lookAt) {
      this.lookTo = this.lookTo ? 0 : (Math.random() < 0.5 ? -1 : 1) * rand(0.03, 0.06);
      this.lookAt = now + (this.lookTo ? rand(900, 1800) : rand(4000, 9000));
    }
    this.look += ((this.calm ? 0 : this.lookTo) * w.idle - this.look) * 0.08;

    ctx.translate(0, -R * 0.05 + Math.sin(t * 0.8) * R * 0.018);

    // pool of light on the "floor"
    ctx.save();
    ctx.translate(0, R * 1.33);
    ctx.scale(1, 0.12);
    let g = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 1.15);
    g.addColorStop(0, c(0.3 + 0.3 * glow));
    g.addColorStop(1, c(0));
    ctx.fillStyle = g;
    ctx.fillRect(-R * 1.2, -R * 1.2, R * 2.4, R * 2.4);
    ctx.restore();

    // halo around the glass, swelling with the sound
    g = ctx.createRadialGradient(0, 0, R * 0.85, 0, 0, R * (1.75 + 0.5 * glow));
    g.addColorStop(0, c(0.3 + 0.35 * glow));
    g.addColorStop(0.3, c(0.1 + 0.14 * glow));
    g.addColorStop(1, c(0));
    ctx.fillStyle = g;
    ctx.fillRect(-u * 1.7, -u * 1.7, u * 3.4, u * 3.4);

    ctx.save();
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, 6.2832);
    ctx.clip();
    // the glass body: lit from the upper left, dark toward the edge
    g = ctx.createRadialGradient(-R * 0.32, -R * 0.42, R * 0.05, 0, 0, R);
    g.addColorStop(0, 'rgba(70, 90, 122, 1)');
    g.addColorStop(0.45, 'rgba(27, 37, 54, 1)');
    g.addColorStop(1, 'rgba(7, 10, 16, 1)');
    ctx.fillStyle = g;
    ctx.fillRect(-R, -R, R * 2, R * 2);
    // the light inside: sits low, rises and brightens with the voice
    ctx.globalCompositeOperation = 'lighter';
    const iy = R * (0.42 - 0.2 * glow);
    g = ctx.createRadialGradient(0, iy, 0, 0, iy, R * (0.95 + 0.35 * glow));
    g.addColorStop(0, c(0.42 + 0.45 * glow + 0.1 * w.thinking));
    g.addColorStop(0.5, c(0.13 + 0.16 * glow));
    g.addColorStop(1, c(0));
    ctx.fillStyle = g;
    ctx.fillRect(-R, -R, R * 2, R * 2);
    // slow drifting wisps, so the inside never looks frozen
    for (let i = 0; i < 3; i++) {
      const a = t * (0.21 + i * 0.07) + i * 2.1;
      const wx = Math.cos(a) * R * 0.42;
      const wy = Math.sin(a * 1.3) * R * 0.3 + R * 0.2;
      g = ctx.createRadialGradient(wx, wy, 0, wx, wy, R * 0.5);
      g.addColorStop(0, c(0.1 + 0.08 * voice));
      g.addColorStop(1, c(0));
      ctx.fillStyle = g;
      ctx.fillRect(-R, -R, R * 2, R * 2);
    }
    // light pooled at the bottom of the glass (soft, no hard edge)
    g = ctx.createRadialGradient(0, R * 1.5, R * 0.3, 0, R * 1.5, R * 1.0);
    g.addColorStop(0, c(0.3 + 0.25 * glow));
    g.addColorStop(1, c(0));
    ctx.fillStyle = g;
    ctx.fillRect(-R, 0, R * 2, R);
    ctx.globalCompositeOperation = 'source-over';
    this.eyes(ctx, R, now);
    ctx.restore();

    // thinking: a mote of light circles the sphere, leaving a short trail
    if (w.thinking > 0.01) {
      for (let i = 0; i < 16; i++) {
        const a = t * 2.4 - i * 0.07;
        if (Math.sin(a) < 0) continue; // behind the glass
        const mx = Math.cos(a) * R * 1.2;
        const my = Math.sin(a) * R * 0.2 + R * 0.12;
        ctx.fillStyle = rgba(mix(color, WHITE, 0.5), w.thinking * (1 - i / 16) * 0.9);
        ctx.beginPath();
        ctx.arc(mx, my, R * 0.03 * (1 - i / 22), 0, 6.2832);
        ctx.fill();
      }
    }

    // rim: cool light from the upper left, the state colour from below
    g = ctx.createLinearGradient(-R, -R, R, R);
    g.addColorStop(0, 'rgba(210, 225, 245, 0.75)');
    g.addColorStop(0.4, 'rgba(120, 140, 170, 0.12)');
    g.addColorStop(1, c(0.75));
    ctx.strokeStyle = g;
    ctx.lineWidth = Math.max(1, R * 0.014);
    ctx.beginPath();
    ctx.arc(0, 0, R - ctx.lineWidth / 2, 0, 6.2832);
    ctx.stroke();

    // reflections on the surface
    ctx.save();
    ctx.translate(-R * 0.42, -R * 0.55);
    ctx.rotate(-0.62);
    ctx.scale(1, 0.42);
    g = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 0.36);
    g.addColorStop(0, 'rgba(255, 255, 255, 0.5)');
    g.addColorStop(0.6, 'rgba(255, 255, 255, 0.1)');
    g.addColorStop(1, 'rgba(255, 255, 255, 0)');
    ctx.fillStyle = g;
    ctx.fillRect(-R * 0.4, -R * 0.4, R * 0.8, R * 0.8);
    ctx.restore();
    ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
    ctx.beginPath();
    ctx.arc(-R * 0.7, -R * 0.2, R * 0.022, 0, 6.2832);
    ctx.fill();
  }

  eyes(ctx, R, now) {
    const { w, lvl, color } = this;
    const blink = 1 - 0.94 * this.blinker.value(now, this.calm);
    // shape per state: half-width, half-height, and how far the lower edge
    // is pulled up (1 = round eye, negative = happy crescent)
    const rx = R * (0.082 * w.idle + 0.098 * w.listening + 0.095 * w.thinking + 0.105 * w.speaking);
    const ry = R * (0.13 * w.idle + 0.165 * w.listening + 0.062 * w.thinking + 0.155 * w.speaking) * blink;
    const lower = w.idle + w.listening + 0.8 * w.thinking - 0.5 * w.speaking;
    const gx = R * (this.look + 0.07 * w.thinking);
    const gy = R * (-0.1 - 0.03 * w.listening - 0.09 * w.thinking - 0.02 * lvl * w.speaking);
    const gap = R * 0.29;
    const fill = rgba(mix(color, WHITE, 0.45), 0.95);
    ctx.save();
    ctx.rotate(-0.07 * w.listening + 0.05 * w.thinking); // a small tilt of the head
    ctx.shadowColor = rgba(color, 1);
    ctx.fillStyle = fill;
    // each shape is drawn twice: a wide soft glow first, then the crisp light on top
    for (const pass of [0, 1]) for (const side of [-1, 1]) {
      ctx.shadowBlur = pass ? R * 0.05 : R * (0.28 + 0.2 * lvl);
      // listening: one eye a touch larger, like a raised brow
      const k = 1 + 0.08 * side * w.listening;
      ctx.beginPath();
      const ex = side * gap + gx;
      const low = gy + 1.33 * ry * k * lower;
      ctx.ellipse(ex, gy, rx * k, Math.max(ry * k, R * 0.008), 0, Math.PI, 0);
      ctx.bezierCurveTo(ex + rx * k, low, ex - rx * k, low, ex - rx * k, gy);
      ctx.fill();
    }
    // speaking: a small bar of light under the eyes that opens with the voice
    if (w.speaking > 0.01) {
      ctx.globalAlpha = w.speaking;
      ctx.beginPath();
      ctx.ellipse(0, R * 0.3, R * (0.085 + 0.05 * lvl), R * (0.014 + 0.085 * lvl), 0, 0, 6.2832);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }
}

export function create(root) {
  return new Glass(root);
}
