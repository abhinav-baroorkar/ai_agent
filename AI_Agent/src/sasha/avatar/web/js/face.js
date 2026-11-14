// The face. CSS handles colour, breathing, the listening ring and the thinking
// orbit (keyed off data-state); this module moves the mouth and blinks.
import { reducedMotion } from './dom.js';

const STATES = ['idle', 'listening', 'thinking', 'speaking'];
// How much the closed mouth curves up in each state.
const SMILE = { idle: 12, listening: 7, thinking: 2, speaking: 8 };
const CX = 200;
const Y = 262;

export class Face {
  constructor(root) {
    this.root = root;
    this.mouth = root.querySelector('.mouth');
    this.eyes = root.querySelector('.eyes');
    this.state = 'idle';
    this.level = () => 0; // 0..1 loudness of whatever is relevant right now
    this.open = 0;
    this.smile = SMILE.idle;
    this.shown = '';
    this.tick = this.tick.bind(this);
    requestAnimationFrame(this.tick);
    this.blinkLater();
  }

  setState(state) {
    if (!STATES.includes(state) || state === this.state) return;
    this.state = state;
    this.root.dataset.state = state;
  }

  setLevelSource(fn) {
    this.level = fn;
  }

  tick() {
    const lvl = Math.max(0, Math.min(1, this.level() || 0));
    const calm = reducedMotion();
    const target = this.state === 'speaking' ? lvl * (calm ? 0.5 : 1) : 0;
    // Open fast, close a little slower: reads as speech instead of flicker.
    this.open += (target - this.open) * (target > this.open ? 0.5 : 0.25);
    this.smile += (SMILE[this.state] - this.smile) * 0.12;
    this.root.style.setProperty('--lvl', lvl.toFixed(3));
    this.draw();
    requestAnimationFrame(this.tick);
  }

  draw() {
    const open = this.open < 0.02 ? 0 : this.open;
    const half = +(33 - open * 7).toFixed(1);
    const top = Y + this.smile * (1 - open) - open * 3;
    const low = Y + this.smile + open * 46;
    const d =
      `M${CX - half} ${Y}Q${CX} ${top.toFixed(1)} ${CX + half} ${Y}` +
      `Q${CX} ${low.toFixed(1)} ${CX - half} ${Y}Z`;
    if (d !== this.shown) {
      this.mouth.setAttribute('d', d);
      this.shown = d;
    }
  }

  blinkLater() {
    setTimeout(() => {
      if (!reducedMotion() && !document.hidden) {
        this.eyes.classList.add('blink');
        setTimeout(() => this.eyes.classList.remove('blink'), 130);
      }
      this.blinkLater();
    }, 2400 + Math.random() * 3800);
  }
}
