// A pretend microphone for ?mock=1: frames of quiet room noise with stretches
// of "speech" (a louder hum), so Talk can be tried without any sound device.
//   ?mock=1             one sentence, then silence
//   ?mock=1&talk=pause  a sentence with a half-second pause in the middle
//   ?mock=1&talk=silent nothing is said at all
//   ?mock=1&talk=stall  the frames stop arriving after half a second (a stalled device)
//   ?mock=1&talk=stall-late  the same, but in the middle of a sentence
// With &mic=real the page uses the browser's microphone instead (in tests, a file).
const PLANS = {
  '': [[300, 1900]],
  pause: [[300, 1300], [1800, 3000]],
  silent: [],
  stall: [],
  'stall-late': [[100, 5000]],
};

export function startMockMic(onFrame) {
  const kind = new URLSearchParams(location.search).get('talk') || '';
  const plan = PLANS[kind] || PLANS[''];
  let ms = 0;
  const timer = setInterval(() => {
    if (kind.startsWith('stall') && ms >= (kind === 'stall' ? 500 : 1000)) return; // no more frames
    const speaking = plan.some(([from, to]) => ms >= from && ms < to);
    const frame = new Int16Array(320);
    for (let i = 0; i < 320; i++) {
      const noise = (Math.random() - 0.5) * 60;
      const voice = speaking ? Math.sin((ms / 1000 + i / 16000) * 2 * Math.PI * 180) * 4200 * (0.6 + 0.4 * Math.sin(ms / 90)) : 0;
      frame[i] = noise + voice;
    }
    ms += 20;
    onFrame(frame.buffer);
  }, 20);
  return () => clearInterval(timer);
}
