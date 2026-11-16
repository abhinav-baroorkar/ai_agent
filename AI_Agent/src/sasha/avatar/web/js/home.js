// Home screen: the face, subtitles, mic, typed fallback and Stop.
import { MOCK } from './api.js';
import { $, setText } from './dom.js';
import { createFace } from './face.js';
import { Conversation } from './socket.js';
import { SpeechPlayer } from './audio-out.js';
import { Mic, micErrorText } from './audio-in.js';
import { startMockMic } from './mock-mic.js';
import { initReadouts, initHistory, watchMicPermission, spokenTail, MIC_BLOCKED_HELP } from './home-extras.js';
import { initTimers } from './timers.js';
import { micSign } from './mic-sign.js';
import { Endpointer, loudness, TUNING } from './endpoint.js';

const LABEL = { idle: 'Ready', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking' };
const params = new URLSearchParams(location.search);
const HELD_FRAMES = 100; // 100 frames of 20 ms

const WOKEN_MS = 8000; // how long it shows listening after a wake (the armed window and a command)

export async function initHome(health, onOnline) {
  const el = {
    status: $('#home-status'), heard: $('#heard'), said: $('#said'), notice: $('#notice'),
    mic: $('#mic'), micLabel: $('#mic-label'), micIcon: $('#mic use'),
    form: $('#say-form'), text: $('#say-text'), cancel: $('#cancel'), hint: $('#voice-hint'), mini: $('#mini-face'), micOn: $('#mic-on'),
  };
  const face = await createFace($('#face'));
  const conv = new Conversation(health.default_bot, { speak: true });
  const player = new SpeechPlayer({ muted: MOCK, onChange: () => render() });
  const mic = new Mic();
  const readouts = initReadouts(health);
  const history = initHistory(health.assistant.name);
  const canListen = !!health.voice.stt && (MOCK || Mic.supported());
  let serverState = 'idle';
  let recording = false;
  let starting = false; // Talk was pressed and the browser has not handed over the microphone yet
  let attempt = 0; // bumped by every stop and every leave; a start that sees it change gives up
  const onHome = () => ['', '#home'].includes(location.hash);
  const realMic = !MOCK || params.get('mic') === 'real'; // mock mode normally uses a pretend microphone
  let ender = null; // listens for the end of speech while recording
  let level = 0; // the microphone level of the last frame, 0..1
  let stopMock = null;
  let backstops = []; // wall-clock timers that close the microphone even if no frames arrive
  let blocked = MOCK && params.get('mic') === 'blocked'; // ?mock=1&mic=blocked shows the blocked state
  let replyText = '';
  let refused = '';

  $('#face').setAttribute('aria-label', `${health.assistant.name}, shown as a sphere of light`);
  el.mic.hidden = !canListen;
  el.hint.hidden = canListen;
  if (!canListen) el.text.placeholder = `Type to talk to ${health.assistant.name}`;

  // The app shell says the wake phrase was heard (`assistant-wake`): the entrance plays,
  // then "listening" while the listener takes the command (visual only; the microphone
  // is the listener's, and nothing here waits for the animation).
  let wokenUntil = 0;
  function woken() {
    wokenUntil = performance.now() + WOKEN_MS;
    if (face.enter) face.enter();
    render();
    setTimeout(render, WOKEN_MS + 50);
  }
  window.addEventListener('assistant-wake', woken);
  // a wake that arrived while the page was still starting (the shell's cold start)
  if (performance.now() - (window.assistantWokeAt ?? -1e9) < 3000) woken();

  face.setLevelSource(() => {
    if (player.active) return player.level();
    if (recording) return level;
    if (MOCK && serverState === 'listening') return 0.4 + 0.35 * Math.sin(performance.now() / 140);
    return 0;
  });

  // The face keeps "speaking" until the sound has finished, even if the server
  // already said idle (speech frames arrive faster than they play).
  function render() {
    const phase = recording && ender ? ender.phase : ''; // waiting, speech, pause or finishing
    const state = player.active ? 'speaking' : serverState;
    // after the wake phrase brought the window up, the being listens with the listener
    const woken = state === 'idle' && performance.now() < wokenUntil;
    const shown = (recording || woken) && state === 'idle' ? 'listening' : state;
    face.setState(shown);
    el.mini.dataset.state = shown;
    el.status.dataset.tone = conv.online ? shown : 'offline';
    el.status.textContent = !conv.online ? 'Offline' : phase === 'finishing' ? 'Finishing…' : LABEL[shown];
    el.cancel.hidden = !(shown === 'thinking' || shown === 'speaking');
    micSign('home', recording || starting); // the rule: the microphone is never open, or opening, without this sign
    el.mic.setAttribute('aria-pressed', String(recording));
    el.mic.dataset.blocked = String(blocked);
    el.mic.dataset.phase = phase;
    const hearing = phase === 'finishing' ? 'Finishing…' : 'Listening…';
    el.micLabel.textContent = blocked ? 'Mic blocked' : starting ? 'Starting…' : recording ? `${hearing} (click to send now)` : 'Talk';
    el.micIcon.setAttribute('href', recording ? '#i-stop' : '#i-mic');
    el.mic.disabled = !conv.online || starting;
    readouts.link(conv.online);
  }

  function notice(text) {
    el.notice.hidden = !text;
    el.notice.textContent = text || '';
  }

  function showSaid(text) {
    replyText = text;
    // three lines' worth of characters at the block's present width
    const perLine = el.said.clientWidth / (parseFloat(getComputedStyle(el.said).fontSize) * 0.5);
    setText(el.said, spokenTail(text, Math.max(80, Math.floor(perLine * 3 * 0.82))));
    history.said(text);
  }

  function newTurn(heard, partial = false) {
    setText(el.heard, heard);
    el.heard.classList.toggle('partial', partial);
    if (partial) return;
    delete el.said.dataset.stopped;
    history.heard(heard);
    showSaid('');
    notice(blocked ? MIC_BLOCKED_HELP : '');
  }

  conv
    .on('online', () => { onOnline(true); render(); })
    .on('offline', () => { stopMic(false); player.stop(); onOnline(false, refused); render(); })
    .on('refused', (m) => { refused = m.message; onOnline(false, refused); })
    .on('state', (m) => {
      // 'thinking' only ever starts a new turn, so speech stopped earlier may play again
      if (m.state === 'thinking' || m.state === 'listening') player.newTurn();
      serverState = m.state;
      render();
    })
    .on('transcript', (m) => newTurn(String(m.text ?? ''), !m.final))
    .on('tool', (m) => history.tool(m))
    .on('reply_delta', (m) => showSaid(replyText + String(m.text ?? '')))
    .on('reply', (m) => { showSaid(String(m.text ?? '')); readouts.answered(m.timing); }) // the final text replaces what streamed
    .on('tts_start', (m) => player.begin(m.sample_rate))
    .on('audio', (buf) => player.push(buf))
    .on('tts_end', () => player.end())
    .on('error', (m) => { stopMic(false); notice(String(m.message ?? 'Something went wrong.')); });

  // Stops the current answer; what was already shown stays, marked as stopped.
  function stopAnswer() {
    const busy = player.active || serverState === 'thinking' || serverState === 'speaking';
    player.stop();
    if (busy && replyText) {
      el.said.dataset.stopped = '';
      history.stopped();
    }
    return busy;
  }

  async function startMic() {
    if (starting || recording) return; // one microphone at a time, however often Talk is pressed
    const mine = ++attempt;
    starting = true;
    render();
    stopAnswer();
    player.unlock();
    // The server ignores sound that arrives before audio_start, and audio_start may
    // have to wait for a running answer to stop. Until it has gone out, frames are
    // held here (at most about two seconds of them) so the first words aren't lost.
    const held = [];
    let started = false;
    let endWaiting = false; // speech ended before audio_start could go out
    ender = new Endpointer();
    const onFrame = (frame) => {
      if (mine !== attempt) return;
      if (started) conv.sendAudio(frame);
      else {
        held.push(frame);
        if (held.length > HELD_FRAMES) held.shift();
      }
      heardFrame(frame, () => (started ? stopMic() : (endWaiting = true)));
    };
    try {
      if (blocked) throw Object.assign(new Error('blocked'), { name: 'NotAllowedError' });
      if (realMic) await mic.start(onFrame);
    } catch (err) {
      if (mine !== attempt) return; // already stopped while starting; nothing to report
      starting = false;
      mic.stop();
      blocked = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      notice(blocked ? MIC_BLOCKED_HELP : micErrorText(err));
      render();
      if (blocked) el.text.focus();
      return;
    }
    // Stopped, left Home or hid the window while the browser was still opening
    // the microphone: close it again at once and do nothing else.
    if (mine !== attempt || !onHome() || document.hidden) {
      if (mine === attempt) {
        starting = false;
        mic.stop();
        render();
      }
      return;
    }
    starting = false;
    blocked = false;
    recording = true;
    setText(el.heard, '');
    notice('');
    conv.whenIdle(() => {
      if (mine !== attempt) return; // stopped before the server was ready
      conv.startAudio();
      held.splice(0).forEach((frame) => conv.sendAudio(frame));
      started = true;
      if (endWaiting) stopMic();
    });
    if (!realMic) stopMock = startMockMic(onFrame);
    // The limits above are counted in audio frames. If frames stop arriving while the
    // microphone is still open (a stalled audio device, a suspended context), these
    // clocks close it anyway.
    backstops = [
      setTimeout(() => { if (mine === attempt && recording) (started ? stopMic() : nothingHeard()); }, TUNING.maxMs + 1000),
      setTimeout(() => { if (mine === attempt && recording && ender.phase === 'waiting') nothingHeard(); }, TUNING.noSpeechMs + 1000),
    ];
    render();
  }

  // Nothing was said: close the microphone, discard the recording, and say so.
  function nothingHeard() {
    stopMic(false);
    conv.waiting = null;
    conv.cancel();
    setText(el.heard, '');
    notice('I didn’t hear anything. Press Talk to try again, or type.');
  }

  // Every microphone frame: update the level shown, and let the end-of-speech
  // listener decide whether the turn is over.
  function heardFrame(frame, send) {
    try {
      const rms = loudness(frame);
      level = Math.min(1, rms * 7);
      const before = ender.phase;
      const verdict = ender.feed(rms);
      el.mic.style.setProperty('--mic-level', level.toFixed(2));
      el.mic.style.setProperty('--mic-closing', ender.closing.toFixed(2));
      if (verdict === 'end' || verdict === 'limit') send(); // he stopped speaking: send it
      else if (verdict === 'nothing') nothingHeard();
      else if (ender.phase !== before) render();
    } catch (err) {
      // Fail closed: if the end-of-speech check itself breaks, the microphone must not
      // stay open and streaming. Send what there is and close it.
      console.warn('Talk: the end-of-speech check failed', err && err.name);
      stopMic();
    }
  }

  function stopMic(tellServer = true) {
    if (!recording && !starting) return;
    attempt++;
    const wasRecording = recording;
    recording = starting = false;
    mic.stop();
    if (stopMock) stopMock();
    stopMock = null;
    backstops.forEach(clearTimeout);
    backstops = [];
    level = 0;
    if (!wasRecording) return render(); // it never got as far as telling the server
    if (tellServer) conv.stopAudio();
    render();
  }

  el.mic.addEventListener('click', () => {
    if (starting) return;
    if (recording) stopMic(); else startMic();
  });
  el.micOn.addEventListener('click', () => stopMic());

  // Leaving Home, hiding the window or closing the page always closes the
  // microphone and silences the answer. What was being recorded is discarded.
  function leave() {
    if (starting) stopMic(false);
    if (recording) {
      stopMic(false);
      conv.waiting = null;
      conv.cancel();
    }
    if (player.active) {
      stopAnswer();
      conv.cancel();
    }
  }
  // Decided from the address, not from what is on screen: this runs before the screens are switched.
  window.addEventListener('hashchange', () => { if (!['', '#home'].includes(location.hash)) leave(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) leave(); });
  window.addEventListener('pagehide', leave);
  if (canListen && !MOCK) {
    watchMicPermission((denied) => {
      blocked = denied;
      notice(denied ? MIC_BLOCKED_HELP : '');
      render();
    });
  }

  el.form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const text = el.text.value.trim();
    if (!text) return;
    if (!conv.online) return notice(refused || 'Offline. Your message was not delivered.');
    stopMic(false);
    stopAnswer();
    player.unlock();
    el.text.value = '';
    // If an answer was running, the server is asked to stop and the message goes out once it is idle.
    conv.whenIdle(() => {
      newTurn(text);
      conv.sendText(text);
    });
  });

  el.cancel.addEventListener('click', () => {
    stopAnswer();
    conv.cancel();
    el.mic.hidden ? el.text.focus() : el.mic.focus();
  });

  // Space toggles the mic and Escape stops, unless the owner is typing.
  document.addEventListener('keydown', (ev) => {
    if (!(ev.target instanceof Element)) return;
    if ($('#home').hidden || ev.target.closest('dialog') || history.isOpen) return;
    if (ev.key === 'Escape' && (recording || starting)) { // throw the recording away
      stopMic(false);
      conv.waiting = null;
      return conv.cancel();
    }
    if (ev.key === 'Escape' && !el.cancel.hidden) return el.cancel.click();
    if (ev.target.closest('input, textarea, select, button, a')) return;
    if (ev.code === 'Space' && canListen && conv.online) {
      ev.preventDefault();
      if (!ev.repeat) el.mic.click(); // a held key must not press Talk again and again
    }
  });

  initTimers({ conv, onRinging: (on) => face.setRinging(on) });
  if (blocked) notice(MIC_BLOCKED_HELP);
  render();
  // Mock mode plays one spoken turn by itself so all four states can be seen.
  if (MOCK && canListen && !blocked && params.get('demo') !== '0') setTimeout(() => !recording && conv.ws && conv.ws.demo && conv.ws.demo(), 1400);
  return { conv };
}
