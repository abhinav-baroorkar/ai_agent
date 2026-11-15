// Hears when the owner has finished speaking, so Talk ends by itself.
//
// It is fed the same 16 kHz, 20 ms frames that go to the server and looks only
// at how loud each one is, against a noise floor that it keeps adjusting: the
// floor drops quickly to any quieter frame and creeps up slowly while nobody
// is speaking. No model and no library; nothing leaves the page.
//
// All the timings to tune are here.
export const TUNING = {
  endSilenceMs: 900, // this much silence after speech ends the turn
  finishingAfterMs: 300, // when the "finishing…" cue appears during that silence
  minSpeechMs: 200, // this much speech must be heard before silence can end the turn
  noSpeechMs: 6000, // nothing at all for this long: stop and say so
  maxMs: 55000, // send before the server's own 60 s cap is reached
  burstFrames: 3, // consecutive loud frames (60 ms) that count as speech, not a click
};
const FRAME_MS = 20;
const FLOOR_MIN = 0.0005;
const FLOOR_MAX = 0.03;

export function loudness(frame) {
  const pcm = new Int16Array(frame);
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
  return pcm.length ? Math.sqrt(sum / pcm.length) / 32768 : 0;
}

export class Endpointer {
  constructor(tuning = TUNING) {
    this.t = tuning;
    this.ms = 0; // how long we have been listening
    this.floor = null; // the noise floor, as RMS between 0 and 1
    this.run = 0; // loud frames in a row
    this.speechMs = 0; // speech heard so far
    this.lastSpeechAt = -1; // when speech was last heard
    this.phase = 'waiting'; // waiting (nothing yet) | speech | pause | finishing
    this.done = null; // 'end' | 'nothing' | 'limit' once decided
  }

  // Feed one frame's loudness. Returns 'end', 'nothing' or 'limit' once, else null.
  feed(rms) {
    if (this.done) return null;
    this.ms += FRAME_MS;
    if (this.floor === null) this.floor = Math.min(Math.max(rms, FLOOR_MIN), FLOOR_MAX);
    const threshold = Math.max(this.floor * 3, this.floor + 0.006, 0.01);
    const loud = rms > threshold;
    this.run = loud ? this.run + 1 : 0;
    const speaking = this.run >= this.t.burstFrames;
    if (rms < this.floor) this.floor += (rms - this.floor) * 0.2; // follow quiet down fast
    else if (!loud) this.floor += (rms - this.floor) * 0.02; // let it rise slowly
    this.floor = Math.min(Math.max(this.floor, FLOOR_MIN), FLOOR_MAX);

    if (speaking) {
      // the frames that made up the burst were speech too
      this.speechMs += this.run === this.t.burstFrames ? FRAME_MS * this.t.burstFrames : FRAME_MS;
      this.lastSpeechAt = this.ms;
    }
    const heard = this.speechMs >= this.t.minSpeechMs;
    const quietFor = this.lastSpeechAt < 0 ? 0 : this.ms - this.lastSpeechAt;
    if (!heard) this.phase = 'waiting';
    else if (speaking || quietFor < FRAME_MS * 2) this.phase = 'speech';
    else this.phase = quietFor >= this.t.finishingAfterMs ? 'finishing' : 'pause';

    if (heard && quietFor >= this.t.endSilenceMs) this.done = 'end';
    else if (!heard && this.ms >= this.t.noSpeechMs) this.done = 'nothing';
    else if (this.ms >= this.t.maxMs) this.done = 'limit';
    return this.done;
  }

  // 0..1: how far through the closing silence we are (for the finishing cue).
  get closing() {
    if (this.phase !== 'finishing') return 0;
    const span = this.t.endSilenceMs - this.t.finishingAfterMs;
    return Math.min(1, (this.ms - this.lastSpeechAt - this.t.finishingAfterMs) / span);
  }
}
