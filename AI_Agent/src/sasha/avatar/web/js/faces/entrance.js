// The "waking" entrance, played when the wake phrase brings the window up: the
// companions gather in from scattered and the being of light grows and brightens into
// place, then listens. Visual only: it never delays the microphone or the command.
// Every number that shapes it lives here, for the design review to tune.
export const ENTRANCE = {
  ms: 520, // total length
  scatter: 3.4, // how far out the companions start (times their usual radius)
  twist: 1.6, // how much they swirl while gathering (radians)
  startScale: 0.3, // the being's size at the start (times its usual size)
  fadeMs: 260, // how long it takes to brighten from nothing
  ease: (x) => 1 - (1 - x) ** 3, // ease-out cubic: fast at first, settling at the end
};

// Progress of an entrance started at `start` (ms), as 0..1 plus its eased value.
export function entranceAt(start, now, e = ENTRANCE) {
  const p = Math.min(1, Math.max(0, (now - start) / e.ms));
  return { p, k: e.ease(p), alpha: Math.min(1, (now - start) / e.fadeMs), done: p >= 1 };
}
