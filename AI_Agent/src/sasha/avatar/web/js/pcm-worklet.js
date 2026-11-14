// Audio worklet: turns the microphone into 16 kHz, 16-bit mono PCM frames of
// 320 samples (20 ms), as the WebSocket contract asks. Averages the samples
// that fall into each output sample, which is a good-enough low-pass for speech.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.acc = 0;
    this.count = 0;
    this.out = new Int16Array(320);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.count++;
      this.pos++;
      if (this.pos < this.ratio) continue;
      this.pos -= this.ratio;
      const s = Math.max(-1, Math.min(1, this.acc / this.count));
      this.acc = 0;
      this.count = 0;
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.out.length) {
        const buf = this.out.buffer.slice(0);
        this.port.postMessage(buf, [buf]);
        this.n = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
