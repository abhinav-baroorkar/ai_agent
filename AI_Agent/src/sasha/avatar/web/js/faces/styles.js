// Breathing styles for a bot's orb. A style changes only how the particles move,
// pulse and glow; the colour is the bot's, and the four states (idle, listening,
// thinking, speaking) are drawn by the sphere itself, the same for every style,
// so a bot always reads the same way. While it listens or thinks the style's own
// motion quietens, so the state shows through.
//
// Each style is a few numbers plus `move`, which nudges one point. `move` runs for
// every point on every frame, so it stays to a couple of sines: cheap at ~12 fps.
// It gets the point (x, y, z on a unit sphere; y points down the screen), the time,
// `amp` (how much of the style to show now) and `voice` (loudness while speaking),
// and returns [dx, dy, dz, light], light being extra brightness from 0 to 1.
const S = Math.sin;
const C = Math.cos;

export const STYLES = {
  glow: {
    reach: 0, // how far it can spread past the sphere
    name: 'Glow', line: 'Calm and steady, the way the main assistant breathes.',
    spin: 1, halo: 1, core: 1, size: 1,
    move: () => null, // the sphere's own breathing, nothing added
  },
  ember: {
    reach: 0.3, // how far it can spread past the sphere
    name: 'Ember', line: 'Sparks lift off the top and flicker, faster when it talks.',
    spin: 0.7, halo: 1.15, core: 1.2, size: 0.95,
    move(p, t, amp, voice) {
      if (p.y > 0.3) return null; // only the upper part sheds sparks
      const life = (t * (0.35 + 0.8 * voice) + p.ph * 0.16) % 1; // each spark's own cycle
      const lift = life * (0.75 + 0.5 * voice) * amp * (0.35 - p.y) * 0.9;
      const sway = S(t * 2.2 + p.ph * 3) * 0.06 * life * amp;
      return [sway, -lift, sway * 0.5, amp * (1 - life) * (0.6 + 0.4 * S(t * 13 + p.ph * 7)) - life * 0.4 * amp];
    },
  },
  tide: {
    reach: 0.25, // how far it can spread past the sphere
    name: 'Tide', line: 'A slow swell that rolls through it from top to bottom.',
    spin: 0.5, halo: 1, core: 1, size: 1,
    move(p, t, amp, voice) {
      const wave = S(p.y * 3.4 - t * 1.4) * (0.2 + 0.12 * voice) * amp;
      return [p.x * wave, 0, p.z * wave, Math.max(0, wave) * 3];
    },
  },
  gale: {
    reach: 0.35, // how far it can spread past the sphere
    name: 'Gale', line: 'Particles stream away to one side in gusts, then gather back.',
    spin: 0.35, halo: 0.95, core: 0.9, size: 0.95,
    move(p, t, amp, voice) {
      const gust = Math.max(0, 0.55 + 0.45 * S(t * 0.8) + 0.3 * S(t * 2.1 + 1)) * (1 + 1.2 * voice);
      const loose = 0.5 + 0.5 * S(p.ph * 9 + p.y * 4); // some particles let go more than others
      const reach = loose * loose * gust * 0.55 * amp * (p.x + 1) * 0.5;
      return [reach, S(t * 3 + p.ph) * 0.03 * amp - reach * 0.1, 0, reach * 1.5];
    },
  },
  storm: {
    reach: 0.1, // how far it can spread past the sphere
    name: 'Storm', line: 'Mostly still, then a quick bright crackle runs across it.',
    spin: 0.2, halo: 0.9, core: 0.8, size: 0.9,
    move(p, t, amp, voice) {
      const every = voice > 0.05 ? 0.8 : 1.7; // crackles come quicker while it talks
      const age = (t % every) / every; // where we are between two crackles
      const strike = Math.floor(t / every); // which crackle this is: it picks its own path
      const path = S(strike * 12.9898) * 0.7;
      const off = Math.abs(p.y - path - 0.22 * S(p.x * 8 + strike * 3) - 0.1 * S(p.z * 13));
      const near = off < 0.11;
      const flash = near && age < 0.28 ? (1 - age / 0.28) * amp : 0;
      const k = flash * 0.14;
      return [p.x * k, p.y * k, p.z * k, flash ? flash * 1.6 : -0.2 * amp];
    },
  },
  mist: {
    reach: 0.45, // how far it can spread past the sphere
    name: 'Mist', line: 'Soft and slow, its edge drifting out into a wide haze.',
    spin: 0.4, halo: 1.3, core: 0.7, size: 1.45,
    move(p, t, amp, voice) {
      if (p.inner) return [0, 0, 0, -0.4 * amp];
      const out = (0.5 + 0.5 * S(t * 0.45 + p.ph * 3)) ** 2 * 0.6 * amp * (1 + 0.5 * voice);
      return [p.x * out, p.y * out - out * 0.15, p.z * out, -0.55 * amp - out * 0.4];
    },
  },
  bloom: {
    reach: 0.4, // how far it can spread past the sphere
    name: 'Bloom', line: 'Opens out into petals and closes again, like a flower.',
    spin: 0.45, halo: 1.05, core: 1.15, size: 1,
    move(p, t, amp, voice) {
      const open = (0.5 - 0.5 * C(t * 0.9)) * (0.55 + 0.3 * voice) * amp;
      const petal = Math.max(0, C(5 * Math.atan2(p.y, p.x))) ** 3; // five petals, facing you
      const k = open * petal;
      return [p.x * k, p.y * k, p.z * k * 0.3, k * 2.2];
    },
  },
  stone: {
    reach: 0, // how far it can spread past the sphere
    name: 'Stone', line: 'Dense and heavy. It hardly moves and breathes very slowly.',
    spin: 0.12, halo: 0.6, core: 0.65, size: 1.35,
    move(p, t, amp, voice) {
      const breath = -0.12 * amp + S(t * 0.3) * 0.03 * amp + voice * 0.05 * S(t * 8 + p.y * 4);
      return [p.x * breath, p.y * breath, p.z * breath, -0.15 * amp];
    },
  },
};

export const STYLE_IDS = Object.keys(STYLES);
export const styleOf = (id) => STYLES[id] || STYLES.glow;
