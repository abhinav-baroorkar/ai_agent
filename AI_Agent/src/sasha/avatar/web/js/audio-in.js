// Microphone capture. start() asks for the mic and calls onFrame with an
// ArrayBuffer of 16 kHz 16-bit mono PCM about every 20 ms.
export class Mic {
  constructor() {
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.loud = 0;
  }

  static supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.AudioWorkletNode);
  }

  async start(onFrame) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url));
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, 'pcm-capture', { numberOfOutputs: 0 });
    this.node.port.onmessage = (ev) => {
      const pcm = new Int16Array(ev.data);
      let sum = 0;
      for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
      this.loud = Math.min(1, (Math.sqrt(sum / pcm.length) / 32768) * 7);
      onFrame(ev.data);
    };
    src.connect(this.node);
  }

  stop() {
    if (this.node) this.node.port.onmessage = null;
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ctx) this.ctx.close().catch(() => {});
    this.ctx = this.stream = this.node = null;
    this.loud = 0;
  }

  level() {
    return this.loud;
  }
}

export function micErrorText(err) {
  if (err && (err.name === 'NotAllowedError' || err.name === 'SecurityError')) {
    return 'The microphone is blocked. Allow it in the browser’s site settings, or type instead.';
  }
  if (err && err.name === 'NotFoundError') return 'No microphone was found. You can type instead.';
  return 'The microphone could not be started. You can type instead.';
}
