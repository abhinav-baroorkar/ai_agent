// Plays the assistant's speech (raw 16-bit mono PCM frames from the server)
// and reports how loud the sound playing right now is, to drive the mouth.
const LEAD_MS = 60; // small buffer before the first frame plays

export class SpeechPlayer {
  constructor({ muted = false, onChange = () => {} } = {}) {
    this.muted = muted;
    this.onChange = onChange;
    this.ctx = null;
    this.rate = 16000;
    this.frames = []; // {t, dur, rms} on the performance.now() clock
    this.sources = new Set();
    this.tEnd = 0;
    this.ctxNext = 0;
    this.active = false;
    this.ended = true;
    this.timer = null;
    this.dropping = false;
  }

  // Browsers only allow sound after a click or key press; call this from one.
  unlock() {
    if (this.muted) return;
    try {
      this.ctx = this.ctx || new AudioContext();
      if (this.ctx.state === 'suspended') this.ctx.resume();
    } catch {
      this.ctx = null;
    }
  }

  // Call when a new turn starts: speech is accepted again after a stop().
  // (Not done in begin(): one answer can carry several tts_start, one per sentence.)
  newTurn() {
    this.dropping = false;
  }

  begin(sampleRate) {
    this.rate = sampleRate || 16000;
    this.ended = false;
  }

  push(buf) {
    if (this.dropping) return; // stopped: ignore what is still arriving for that answer
    const pcm = new Int16Array(buf, 0, buf.byteLength >> 1);
    if (!pcm.length) return;
    const samples = new Float32Array(pcm.length);
    let sum = 0;
    for (let i = 0; i < pcm.length; i++) {
      samples[i] = pcm[i] / 32768;
      sum += samples[i] * samples[i];
    }
    const dur = (pcm.length / this.rate) * 1000;
    const now = performance.now();
    if (this.tEnd < now) this.tEnd = now + LEAD_MS;
    this.frames.push({ t: this.tEnd, dur, rms: Math.sqrt(sum / pcm.length) });
    this.tEnd += dur;
    this.play(samples);
    if (!this.active) {
      this.active = true;
      this.onChange(true);
    }
    this.arm();
  }

  play(samples) {
    const ctx = this.ctx;
    if (this.muted || !ctx || ctx.state !== 'running') return;
    const buffer = ctx.createBuffer(1, samples.length, this.rate);
    buffer.copyToChannel(samples, 0);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    if (this.ctxNext < ctx.currentTime) this.ctxNext = ctx.currentTime + LEAD_MS / 1000;
    src.start(this.ctxNext);
    this.ctxNext += buffer.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  end() {
    this.ended = true;
    this.arm();
  }

  // Finish only when the server said the speech is complete and it has all played.
  arm() {
    clearTimeout(this.timer);
    if (!this.active) return;
    this.timer = setTimeout(() => {
      if (!this.ended) return;
      this.finish();
    }, Math.max(0, this.tEnd - performance.now()) + 40);
  }

  stop() {
    this.sources.forEach((s) => {
      try { s.stop(); } catch { /* already stopped */ }
    });
    this.sources.clear();
    this.ended = true;
    this.dropping = true;
    this.finish();
  }

  finish() {
    clearTimeout(this.timer);
    this.frames = [];
    this.tEnd = 0;
    this.ctxNext = 0;
    if (this.active) {
      this.active = false;
      this.onChange(false);
    }
  }

  // 0..1 loudness of the frame playing now.
  level() {
    const now = performance.now();
    while (this.frames.length && this.frames[0].t + this.frames[0].dur < now) this.frames.shift();
    const f = this.frames[0];
    return f && f.t <= now ? Math.min(1, f.rms * 5.5) : 0;
  }
}
