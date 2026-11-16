// The "waking" entrance, played when the wake phrase brings the window up: the
// companions gather in from scattered and the being of light grows and brightens into
// place, then listens. A calm arrival, not a pop (design review, 2026-11-16). When the
// window was already in view on Home there is no shrink: it only brightens. Visual
// only: it never delays the microphone or the command.
// Every number that shapes it lives here, for the design review to tune.
export const ENTRANCE = {
  ms: 900, // total length
  scatter: 2.2, // how far out the companions start (times their usual radius)
  twist: 1.0, // how much they swirl while gathering (radians)
  startScale: 0.6, // the being's size at the start (times its usual size)
  fadeMs: 450, // how long it takes to brighten from nothing (a full entrance)
  brightenFrom: 0.55, // where the brighten-only version starts (window already in view)
  ease: (x) => 1 - (1 - x) ** 2, // ease-out quadratic: gentle start of the settle
};

// The entrance started at `start` (ms). `full`: gather and grow; otherwise only brighten.
// k: how far it has settled (0..1, eased); bright: the brightness to draw at (0..1).
export function entranceAt(start, now, full = true, e = ENTRANCE) {
  const p = Math.min(1, Math.max(0, (now - start) / e.ms));
  const f = Math.min(1, Math.max(0, (now - start) / e.fadeMs));
  const from = full ? 0 : e.brightenFrom;
  return {
    p,
    k: full ? e.ease(p) : 1,
    bright: from + (1 - from) * e.ease(f),
    done: p >= 1,
  };
}
