// Small helpers shared by the face modules.
import { rgba } from './stage.js';

export const WHITE = [255, 250, 240];

// Soft round light, centred at (x, y).
export function glow(ctx, x, y, r, color, alpha) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, rgba(color, alpha));
  g.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = g;
  ctx.fillRect(x - r, y - r, r * 2, r * 2);
}

// A critically simple spring: value chases goal with a little overshoot.
export class Spring {
  constructor(value = 0, stiffness = 40, damping = 8) {
    this.v = value;
    this.vel = 0;
    this.k = stiffness;
    this.c = damping;
  }

  step(goal, dt) {
    this.vel += ((goal - this.v) * this.k - this.vel * this.c) * dt;
    this.v += this.vel * dt;
    return this.v;
  }
}
