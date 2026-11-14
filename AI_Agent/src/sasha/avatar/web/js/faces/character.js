// Concept 2: an illustrated character. The artwork is in character-art.js;
// this module gives it life: blinking, glances, a small head movement per
// state, brows, and a mouth with several shapes driven by the loudness.
import { Mood, Blinker, rand } from './stage.js';
import { ART } from './character-art.js';

class Character extends Mood {
  constructor(root) {
    super(root);
    root.insertAdjacentHTML('beforeend', ART); // static artwork, no outside text
    const q = (sel) => root.querySelector(sel);
    this.el = {
      head: q('.head'), body: q('.body'), mouth: q('.mouth'), brows: [q('.brow.l'), q('.brow.r')],
      balls: [...root.querySelectorAll('.ball')], pupils: [...root.querySelectorAll('.pupil')], lids: [...root.querySelectorAll('.lid')],
    };
    this.blinker = new Blinker();
    this.look = { x: 0, y: 0, tx: 0, ty: 0, at: performance.now() + 4000 };
    this.open = 0;
    const frame = (now) => {
      this.step(now);
      if (!document.hidden) this.pose(now);
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  pose(now) {
    const { w, lvl, t, el, look } = this;
    this.root.style.setProperty('--glow', `rgb(${this.color.map(Math.round).join(',')})`);

    // head: leans in to listen, tips the other way to think, nods a little with the voice
    const tilt = -3.2 * w.listening + 2.6 * w.thinking + Math.sin(t * 0.5) * 0.7 + Math.sin(t * 6) * 0.5 * lvl * w.speaking;
    const bob = Math.sin(t * 0.9) * 1.6 - lvl * 2 * w.speaking;
    el.head.setAttribute('transform', `translate(0 ${bob.toFixed(2)}) rotate(${tilt.toFixed(2)} 200 300)`);
    el.body.setAttribute('transform', `translate(0 ${(Math.sin(t * 0.9 - 0.6) * 1.2).toFixed(2)})`);

    // eyes: idle glances, up and aside when thinking, steady on you when listening
    if (now > look.at) {
      const away = look.tx === 0 && look.ty === 0;
      look.tx = away ? rand(-3, 3) : 0;
      look.ty = away ? rand(-1.5, 1.5) : 0;
      look.at = now + (away ? rand(700, 1600) : rand(2500, 7000));
    }
    const still = this.calm ? 0 : w.idle + 0.5 * w.speaking;
    look.x += (look.tx * still + 3.6 * w.thinking - look.x) * 0.1;
    look.y += (look.ty * still - 4.2 * w.thinking - look.y) * 0.1;
    const blink = this.blinker.value(now, this.calm);
    const wide = 1 + 0.07 * w.listening - 0.1 * w.thinking;
    for (let i = 0; i < 2; i++) {
      el.balls[i].setAttribute('transform', `scale(${wide.toFixed(3)} ${(wide * (1 - 0.93 * blink)).toFixed(3)})`);
      el.pupils[i].setAttribute('transform', `translate(${look.x.toFixed(2)} ${look.y.toFixed(2)})`);
      el.lids[i].setAttribute('transform', `translate(0 ${(blink * 13 - 2 * w.listening + 3 * w.thinking).toFixed(2)})`);
    }
    const lift = [-4.5 * w.listening + 2 * w.thinking - 2.5 * lvl * w.speaking, -4.5 * w.listening - 5 * w.thinking - 2.5 * lvl * w.speaking];
    el.brows[0].setAttribute('transform', `translate(0 ${lift[0].toFixed(2)}) rotate(${(4 * w.thinking).toFixed(2)} 164 157)`);
    el.brows[1].setAttribute('transform', `translate(0 ${lift[1].toFixed(2)}) rotate(${(-5 * w.thinking).toFixed(2)} 236 157)`);

    // mouth: closed smile at rest; while speaking it opens with the loudness
    // and wanders between a wide and a rounded shape so it never just flaps
    const target = w.speaking * lvl;
    this.open += (target - this.open) * (target > this.open ? 0.5 : 0.28);
    const open = this.open < 0.03 ? 0 : this.open;
    const round = 0.5 + 0.5 * Math.sin(t * 7.3) * Math.sin(t * 3.1);
    const smile = 8 * w.idle + 6 * w.listening + 1.5 * w.thinking + 5 * w.speaking;
    const half = 21 - 8 * round * open - 4 * w.thinking;
    const cx = 200 + 3 * w.thinking;
    const y = 257;
    const top = y + smile * (1 - open) - 3 * open;
    const low = y + smile + open * (15 + 9 * round);
    el.mouth.setAttribute('d',
      `M${(cx - half).toFixed(1)} ${y}Q${cx} ${top.toFixed(1)} ${(cx + half).toFixed(1)} ${y}` +
      `Q${cx} ${low.toFixed(1)} ${(cx - half).toFixed(1)} ${y}Z`);
  }
}

export function create(root) {
  return new Character(root);
}
