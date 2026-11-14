// Shared base for the canvas faces: sizing, the frame loop, and smooth
// blending between the four states so no change ever snaps.
import { reducedMotion } from '../dom.js';

export const COLORS = {
  idle: [243, 201, 139],
  listening: [114, 214, 196],
  thinking: [179, 166, 245],
  speaking: [243, 201, 139],
};
export const STATES = Object.keys(COLORS);
export const clamp = (v, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));
export const rand = (a, b) => a + Math.random() * (b - a);
export const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${clamp(a).toFixed(3)})`;
export const mix = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

// Blends the state weights, colour and loudness; used by canvas and SVG faces alike.
export class Mood {
  constructor(root) {
    this.root = root;
    this.state = 'idle';
    this.w = { idle: 1, listening: 0, thinking: 0, speaking: 0 };
    this.color = [...COLORS.idle];
    this.level = () => 0;
    this.lvl = 0; // quick follow of the loudness
    this.trail = 0; // slow follow, for glows
    this.t = 0; // seconds of animation time (frozen under reduced motion)
    this.dt = 0;
    this.calm = false;
    this.last = performance.now();
  }

  setState(state) {
    if (!STATES.includes(state)) return;
    this.state = state;
    this.root.dataset.state = state;
  }

  setLevelSource(fn) {
    this.level = fn;
  }

  step(now) {
    const dt = clamp((now - this.last) / 1000, 0, 0.05);
    this.last = now;
    this.calm = reducedMotion();
    this.dt = this.calm ? 0 : dt; // reduced motion: nothing drifts, spins or flows
    this.t += this.dt;
    const color = [0, 0, 0];
    for (const s of STATES) {
      this.w[s] += ((s === this.state ? 1 : 0) - this.w[s]) * Math.min(1, dt * 4.5);
      for (let i = 0; i < 3; i++) color[i] += COLORS[s][i] * this.w[s];
    }
    this.color = color;
    const raw = clamp(this.level() || 0) * (this.calm ? 0.35 : 1);
    this.lvl += (raw - this.lvl) * Math.min(1, dt * (raw > this.lvl ? 22 : 9));
    this.trail += (this.lvl - this.trail) * Math.min(1, dt * 3);
  }
}

export class Stage extends Mood {
  constructor(root, overscan = 1.6) {
    super(root);
    this.overscan = overscan;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'face-canvas';
    this.canvas.setAttribute('aria-hidden', 'true');
    root.append(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.unit = 1; // half the face box, in canvas pixels
    new ResizeObserver(() => this.resize()).observe(root);
    this.resize();
    const frame = (now) => {
      this.step(now);
      // Only draw when it can be seen: not in a hidden window, not behind Bots or Settings.
      if (!document.hidden && this.root.offsetParent !== null && this.unit > 4) {
        const { ctx, canvas } = this;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.translate(canvas.width / 2, canvas.height / 2);
        this.draw(ctx, this.unit);
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  resize() {
    const box = Math.min(this.root.clientWidth, this.root.clientHeight);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const css = Math.round(box * this.overscan);
    this.canvas.style.width = this.canvas.style.height = `${css}px`;
    this.canvas.width = this.canvas.height = Math.max(2, Math.round(css * dpr));
    this.unit = (box * dpr) / 2;
  }

  draw() {}
}
