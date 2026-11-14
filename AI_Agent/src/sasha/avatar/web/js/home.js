// Home screen: the face, subtitles, mic, typed fallback and Stop.
import { MOCK } from './api.js';
import { $, setText } from './dom.js';
import { createFace } from './face.js';
import { Conversation } from './socket.js';
import { SpeechPlayer } from './audio-out.js';
import { Mic, micErrorText } from './audio-in.js';

const LABEL = { idle: 'Ready', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking' };

export async function initHome(health, onOnline) {
  const el = {
    status: $('#home-status'), heard: $('#heard'), said: $('#said'), notice: $('#notice'),
    mic: $('#mic'), micLabel: $('#mic-label'), micIcon: $('#mic use'),
    form: $('#say-form'), text: $('#say-text'), cancel: $('#cancel'), hint: $('#voice-hint'),
  };
  const face = await createFace($('#face'));
  const conv = new Conversation(health.default_bot, { speak: true });
  const player = new SpeechPlayer({ muted: MOCK, onChange: () => render() });
  const mic = new Mic();
  const canListen = !!health.voice.stt && (MOCK || Mic.supported());
  let serverState = 'idle';
  let recording = false;
  let replyText = '';

  $('#face').setAttribute('aria-label', `${health.assistant.name}’s face`);
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
    el.status.dataset.tone = conv.online ? shown : 'offline';
    el.status.textContent = conv.online ? LABEL[shown] : 'Offline';
    el.cancel.hidden = !(shown === 'thinking' || shown === 'speaking');
    el.mic.setAttribute('aria-pressed', String(recording));
    el.micLabel.textContent = recording ? 'Done talking' : 'Talk';
    el.micIcon.setAttribute('href', recording ? '#i-stop' : '#i-mic');
    el.mic.disabled = !conv.online;
  }

  function notice(text) {
    el.notice.hidden = !text;
    el.notice.textContent = text || '';
  }

  function newTurn(heard, partial = false) {
    setText(el.heard, heard);
    el.heard.classList.toggle('partial', partial);
    if (!partial) {
      replyText = '';
      setText(el.said, '');
    }
    notice('');
  }

  conv
    .on('online', () => { onOnline(true); render(); })
    .on('offline', () => { stopMic(false); player.stop(); onOnline(false); render(); })
    .on('state', (m) => { serverState = m.state; render(); })
    .on('transcript', (m) => newTurn(m.text, !m.final))
    .on('reply_delta', (m) => { replyText += m.text; setText(el.said, replyText); })
    .on('reply', (m) => { replyText = m.text; setText(el.said, replyText); })
    .on('tts_start', (m) => player.begin(m.sample_rate))
    .on('audio', (buf) => player.push(buf))
    .on('tts_end', () => player.end())
    .on('error', (m) => { stopMic(false); notice(m.message); });

  function interrupt() {
    if (player.active || serverState !== 'idle') conv.cancel();
    player.stop();
  }

  async function startMic() {
    interrupt();
    player.unlock();
    try {
      if (!MOCK) await mic.start((frame) => conv.sendAudio(frame));
    } catch (err) {
      mic.stop();
      notice(micErrorText(err));
      return;
    }
    recording = true;
    setText(el.heard, '');
    setText(el.said, '');
    conv.startAudio();
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

  el.form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const text = el.text.value.trim();
    if (!text) return;
    stopMic(false);
    interrupt();
    player.unlock();
    if (!conv.online) return notice('Offline. Your message was not delivered.');
    el.text.value = '';
    newTurn(text);
    conv.sendText(text);
  });

  el.cancel.addEventListener('click', () => {
    conv.cancel();
    player.stop();
    el.mic.hidden ? el.text.focus() : el.mic.focus();
  });

  // Space toggles the mic and Escape stops, unless the owner is typing.
  document.addEventListener('keydown', (ev) => {
    if ($('#home').hidden || ev.target.closest('dialog')) return;
    if (ev.key === 'Escape' && !el.cancel.hidden) return el.cancel.click();
    if (ev.target.closest('input, textarea, select, button, a')) return;
    if (ev.code === 'Space' && canListen && conv.online) { ev.preventDefault(); el.mic.click(); }
  });

  render();
  // Mock mode plays one spoken turn by itself so all four states can be seen.
  if (MOCK && canListen) setTimeout(() => !recording && conv.ws && conv.ws.demo && conv.ws.demo(), 1400);
  return { conv };
}
