// Speaking a message into a bot chat. Same capture, same end-of-speech
// detection and the same rules as Talk on Home: the "Microphone is on" sign
// shows the whole time, a start that is overtaken closes what it opened, and
// leaving the chat, hiding the window or closing the page discards the recording.
// The reply stays text: chat conversations are not spoken.
import { MOCK } from './api.js';
import { $ } from './dom.js';
import { Mic, micErrorText } from './audio-in.js';
import { startMockMic } from './mock-mic.js';
import { Endpointer, loudness, TUNING } from './endpoint.js';
import { micSign } from './mic-sign.js';

const HELD_FRAMES = 100; // about two seconds held until audio_start has gone out
const realMic = !MOCK || new URLSearchParams(location.search).get('mic') === 'real';
const mic = new Mic(); // one microphone for every chat
let current = null; // the chat that has it now

export const voiceSupported = (health) => health.voice.stt && (MOCK || Mic.supported());

export class VoiceInput {
  constructor({ conv, owner, isVisible, onChange, onNotice }) {
    Object.assign(this, { conv, owner, isVisible, onChange, onNotice });
    this.recording = false;
    this.starting = false;
    this.attempt = 0;
    this.ender = null;
    this.backstops = [];
    this.stopMock = null;
    this.sent = false; // audio_start has gone out for the recording in progress
  }

  get on() { return this.recording || this.starting; }
  get phase() { return this.recording && this.ender ? this.ender.phase : ''; }

  async start() {
    if (this.on) return;
    if (current && current !== this) current.cancel();
    current = this;
    const mine = ++this.attempt;
    this.starting = true;
    this.changed();
    const held = [];
    let started = false;
    let endWaiting = false;
    this.ender = new Endpointer();
    const onFrame = (frame) => {
      if (mine !== this.attempt) return;
      if (started) this.conv.sendAudio(frame);
      else {
        held.push(frame);
        if (held.length > HELD_FRAMES) held.shift();
      }
      this.hear(frame, () => (started ? this.send() : (endWaiting = true)));
    };
    try {
      if (realMic) await mic.start(onFrame);
    } catch (err) {
      if (mine !== this.attempt) return;
      this.starting = false;
      mic.stop();
      this.onNotice(micErrorText(err));
      return this.changed();
    }
    // overtaken, or no longer on screen, while the browser was opening the microphone
    if (mine !== this.attempt || document.hidden || !this.isVisible()) {
      if (mine === this.attempt) {
        this.starting = false;
        mic.stop();
        this.changed();
      }
      return undefined;
    }
    this.starting = false;
    this.recording = true;
    // "Send now" before the server was ready: send what was said once it is idle.
    this.flush = () => {
      if (!held.length) return;
      this.conv.startAudio();
      held.splice(0).forEach((frame) => this.conv.sendAudio(frame));
      this.conv.stopAudio();
    };
    this.conv.whenIdle(() => {
      if (mine !== this.attempt) return;
      this.conv.startAudio();
      this.sent = true;
      held.splice(0).forEach((frame) => this.conv.sendAudio(frame));
      started = true;
      if (endWaiting) this.send();
    });
    if (!realMic) this.stopMock = startMockMic(onFrame);
    // wall-clock backstops, in case frames stop arriving while the microphone is open
    this.backstops = [
      setTimeout(() => { if (mine === this.attempt && this.recording) (started ? this.send() : this.cancel()); }, TUNING.maxMs + 1000),
      setTimeout(() => { if (mine === this.attempt && this.recording && this.ender.phase === 'waiting') this.nothing(); }, TUNING.noSpeechMs + 1000),
    ];
    return this.changed();
  }

  hear(frame, send) {
    try {
      const before = this.ender.phase;
      const verdict = this.ender.feed(loudness(frame));
      if (verdict === 'end' || verdict === 'limit') send();
      else if (verdict === 'nothing') this.nothing();
      else if (this.ender.phase !== before) this.changed();
    } catch (err) { // fail closed
      console.warn('Chat microphone: the end-of-speech check failed', err && err.name);
      this.send();
    }
  }

  send() { this.close(true); }
  cancel() { this.close(false); }

  nothing() {
    this.cancel();
    this.onNotice('I didn’t hear anything. Press the microphone to try again, or type.');
  }

  close(tellServer) {
    if (!this.on) return;
    this.attempt += 1;
    const wasRecording = this.recording;
    this.recording = this.starting = false;
    mic.stop();
    if (this.stopMock) this.stopMock();
    this.stopMock = null;
    this.backstops.forEach(clearTimeout);
    this.backstops = [];
    const sent = this.sent;
    this.sent = false;
    // still waiting for the server: sending queues what was said for when it is idle; discarding drops it
    if (wasRecording && !sent) this.conv.waiting = tellServer ? this.flush : null;
    else if (wasRecording && tellServer) this.conv.stopAudio();
    else if (wasRecording) this.conv.cancel(); // discard what the server has heard so far
    if (current === this) current = null;
    this.changed();
  }

  changed() {
    micSign(this.owner, this.on); // never open, or opening, without the sign
    this.onChange(this);
  }
}

// The sign's own Stop sends what was said. Leaving, hiding or closing discards it.
$('#mic-on').addEventListener('click', () => current && current.send());
const discard = () => current && current.cancel();
window.addEventListener('hashchange', () => { if (location.hash !== '#chat') discard(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) discard(); });
window.addEventListener('pagehide', discard);
