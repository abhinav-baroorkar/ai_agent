// Microphone capture. start() asks for the mic and calls onFrame with an
// ArrayBuffer of 16 kHz 16-bit mono PCM about every 20 ms.
//
// The rule this file keeps: after stop() returns, nothing this object ever
// opened is still live, whatever order start() and stop() were called in and
// even if a start() is still waiting for the browser's permission prompt.
export class Mic {
  constructor() {
    this.open = new Set(); // every {stream, ctx, node} opened and not yet closed
    this.run = 0; // bumped by every start() and stop(); a start() that sees it change gives up
    this.loud = 0;
  }

  static supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.AudioWorkletNode);
  }

  // Resolves true once capture is running, false if it was stopped or
  // restarted while starting (it has then closed everything it opened).
  async start(onFrame) {
    this.stop(); // never two captures at once
    const run = this.run;
    const part = { stream: null, ctx: null, node: null };
    this.open.add(part);
    try {
      part.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (run !== this.run) return this.close(part);
      part.ctx = new AudioContext();
      await part.ctx.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url));
      if (run !== this.run) return this.close(part);
      const src = part.ctx.createMediaStreamSource(part.stream);
      part.node = new AudioWorkletNode(part.ctx, 'pcm-capture', { numberOfOutputs: 0 });
      part.node.port.onmessage = (ev) => {
        if (run !== this.run) return;
        const pcm = new Int16Array(ev.data);
        let sum = 0;
        for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
        this.loud = Math.min(1, (Math.sqrt(sum / pcm.length) / 32768) * 7);
        onFrame(ev.data);
      };
      src.connect(part.node);
      return true;
    } catch (err) {
      this.close(part);
      throw err;
    }
  }

  close(part) {
    this.open.delete(part);
    if (part.node) {
      part.node.port.onmessage = null;
      part.node.disconnect();
    }
    if (part.stream) part.stream.getTracks().forEach((t) => t.stop());
    if (part.ctx && part.ctx.state !== 'closed') part.ctx.close().catch(() => {});
    return false;
  }

  stop() {
    this.run++;
    [...this.open].forEach((part) => this.close(part));
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
