// Home screen: the face, subtitles, mic, typed fallback and Stop.
import { MOCK } from './api.js';
import { $, setText } from './dom.js';
import { createFace } from './face.js';
import { Conversation } from './socket.js';
import { SpeechPlayer } from './audio-out.js';
import { Mic, micErrorText } from './audio-in.js';
import { initReadouts, initHistory, watchMicPermission, MIC_BLOCKED_HELP } from './home-extras.js';

const LABEL = { idle: 'Ready', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking' };
const params = new URLSearchParams(location.search);
const HELD_FRAMES = 100; // 100 frames of 20 ms

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
  let blocked = MOCK && params.get('mic') === 'blocked'; // ?mock=1&mic=blocked shows the blocked state
  let replyText = '';
  let refused = '';

  $('#face').setAttribute('aria-label', `${health.assistant.name}, shown as a sphere of light`);
  el.mic.hidden = !canListen;
  el.hint.hidden = canListen;
  if (!canListen) el.text.placeholder = `Type to talk to ${health.assistant.name}`;

  face.setLevelSource(() => {
    if (player.active) return player.level();
    if (MOCK && serverState === 'listening') return 0.4 + 0.35 * Math.sin(performance.now() / 140);
    if (recording) return mic.level();
    return 0;
  });

  // The face keeps "speaking" until the sound has finished, even if the server
  // already said idle (speech frames arrive faster than they play).
  function render() {
    const state = player.active ? 'speaking' : serverState;
    const shown = recording && state === 'idle' ? 'listening' : state;
    face.setState(shown);
    el.mini.dataset.state = shown;
    el.status.dataset.tone = conv.online ? shown : 'offline';
    el.status.textContent = conv.online ? LABEL[shown] : 'Offline';
    el.cancel.hidden = !(shown === 'thinking' || shown === 'speaking');
    el.micOn.hidden = !recording; // the rule: the microphone is never open without this sign
    el.mic.setAttribute('aria-pressed', String(recording));
    el.mic.dataset.blocked = String(blocked);
    el.micLabel.textContent = blocked ? 'Mic blocked' : recording ? 'Done talking' : 'Talk';
    el.micIcon.setAttribute('href', recording ? '#i-stop' : '#i-mic');
    el.mic.disabled = !conv.online;
    readouts.link(conv.online);
  }

  function notice(text) {
    el.notice.hidden = !text;
    el.notice.textContent = text || '';
  }

  function showSaid(text) {
    replyText = text;
    setText(el.said, text);
    el.said.classList.toggle('long', el.said.scrollHeight > el.said.clientHeight + 2);
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
    .on('state', (m) => { serverState = m.state; render(); })
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
    stopAnswer();
    player.unlock();
    // The server ignores sound that arrives before audio_start, and audio_start may
    // have to wait for a running answer to stop. Until it has gone out, frames are
    // held here (at most about two seconds of them) so the first words aren't lost.
    const held = [];
    let started = false;
    const onFrame = (frame) => {
      if (started) return conv.sendAudio(frame);
      held.push(frame);
      if (held.length > HELD_FRAMES) held.shift();
    };
    try {
      if (blocked) throw Object.assign(new Error('blocked'), { name: 'NotAllowedError' });
      if (!MOCK) await mic.start(onFrame);
    } catch (err) {
      mic.stop();
      blocked = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
      notice(blocked ? MIC_BLOCKED_HELP : micErrorText(err));
      render();
      if (blocked) el.text.focus();
      return;
    }
    blocked = false;
    recording = true;
    setText(el.heard, '');
    notice('');
    conv.whenIdle(() => {
      if (!recording) return; // stopped before the server was ready
      conv.startAudio();
      held.splice(0).forEach((frame) => conv.sendAudio(frame));
      started = true;
    });
    render();
  }

  function stopMic(tellServer = true) {
    if (!recording) return;
    recording = false;
    mic.stop();
    if (tellServer) conv.stopAudio();
    render();
  }

  el.mic.addEventListener('click', () => (recording ? stopMic() : startMic()));
  el.micOn.addEventListener('click', () => stopMic());

  // Leaving Home, hiding the window or closing the page always closes the
  // microphone and silences the answer. What was being recorded is discarded.
  function leave() {
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
    if ($('#home').hidden || ev.target.closest('dialog') || history.isOpen) return;
    if (ev.key === 'Escape' && !el.cancel.hidden) return el.cancel.click();
    if (ev.target.closest('input, textarea, select, button, a')) return;
    if (ev.code === 'Space' && canListen && conv.online) { ev.preventDefault(); el.mic.click(); }
  });

  if (blocked) notice(MIC_BLOCKED_HELP);
  render();
  // Mock mode plays one spoken turn by itself so all four states can be seen.
  if (MOCK && canListen && !blocked) setTimeout(() => !recording && conv.ws && conv.ws.demo && conv.ws.demo(), 1400);
  return { conv };
}
