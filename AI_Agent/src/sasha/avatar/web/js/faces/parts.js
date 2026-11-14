// Drawing helpers shared by the face modules. They expect the canvas to be
// scaled so that 1 unit is the character's radius; `px` is that radius in
// real pixels (shadow blur is not affected by the canvas scale).
import { rgba, mix } from './stage.js';

export const WHITE = [255, 250, 240];

// Soft round light, centred at (x, y).
export function glow(ctx, x, y, r, color, alpha) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, rgba(color, alpha));
  g.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = g;
  ctx.fillRect(x - r, y - r, r * 2, r * 2);
}

// An eye made of light. `lower` shapes the bottom edge: 1 is a round eye,
// about -0.5 is a pleased crescent. Drawn twice: a wide glow, then the crisp shape.
export function lightEye(ctx, px, { x, y, rx, ry, lower = 1, rot = 0, color, bright = 0 }) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.fillStyle = rgba(mix(color, WHITE, 0.72), 0.97);
  ctx.shadowColor = rgba(color, 1);
  const low = 1.33 * ry * lower;
  for (const pass of [0, 1]) {
    ctx.shadowBlur = pass ? px * 0.05 : px * (0.25 + 0.2 * bright);
    ctx.beginPath();
    ctx.ellipse(0, 0, rx, Math.max(ry, 0.006), 0, Math.PI, 0);
    ctx.bezierCurveTo(rx, low, -rx, low, -rx, 0);
    ctx.fill();
  }
  ctx.restore();
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
