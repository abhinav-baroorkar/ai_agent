# Research notes

Dated observations that could feed the paper. Keep numbers, setup and model versions.

## 2026-11-08

- Telemetry schema v1 is in place from Phase 0: per-stage latency, Ollama token
  counts and tokens/s, model digests, RAM/VRAM per request. Baseline for every
  later comparison (big vs small model, cascade vs direct, fast path vs LLM).
- Idle VRAM on the PC with nothing loaded: ~2.0–2.2 GB of 12.2 GB used (Windows
  desktop). Real headroom for models is about 10 GB, not 12.

## 2026-11-14

Laptop, RTX 5070 Laptop 8 GB, power profile "quiet" (so CPU numbers are pessimistic).
3 runs, medians, 8 CPU threads.

| Stage | Device | Load (s) | Latency (ms) | Real-time factor | RAM +MB | VRAM +MB |
| --- | --- | --- | --- | --- | --- | --- |
| TTS Kokoro-82M | CPU | 1.35 | 457 to first audio | 0.25 | 499 | 0 |
| STT Whisper small float16 | GPU | 1.34 | 127 | 0.049 | 1183 | 785 |
| STT Whisper small int8 | CPU | 0.56 | 1751 | 0.65 | 465 | 0 |

- **Brain server with `qwen3.5:9b`:** first streamed text 6.1 s cold (model load), 150 to
  170 ms warm; 49 to 55 tokens/s; cancel returns to idle in 23 ms.
- **VRAM budget on the 8 GB card:** LLM 5.2 GB at 8k context (fully on GPU) + STT 0.8 GB
  = 7.1 of 8.15 GB used. About 1 GB headroom. Risk: a bigger context could push LLM
  layers to the CPU.
- **Surprise:** with two supported languages, Whisper's language detection costs a second
  encoder pass (2.1 s vs 1.2 s on CPU; negligible on GPU).
- **Surprise:** on this hybrid CPU, 8 threads beat 16 and the default for Kokoro; the
  efficiency cores slow it down.
- Not yet measured: the full round trip (hear, think, speak) end to end.
- **Brain server, final:** short replies take 225 to 312 ms in total at 49 to 55 tokens/s
  (`qwen3.5:9b`, laptop, warm).

## 2026-11-14 (evening): full spoken turn, and load order

A full spoken turn works end to end on the laptop with synthetic speech streamed by a
script. Not yet measured with a real microphone in the window.

- Warm medians from the end of speech: transcript 126 ms, first reply text 254 ms, first
  audio 956 ms, done in about 1.5 s.
- Cold first turn after startup: first audio about 1.5 s (was 9.4 s before the warm-up).
- **Finding: load order decides GPU placement.** With speech-to-text loaded on the GPU
  first, the LLM spilled 17% onto the CPU and ran at 36 tokens/s. With the LLM loaded
  first everything fits (7.35 of 8.15 GB) and it runs at 58 tokens/s.
- A/B: starting the first piece of an answer early (first sentence as soon as it ends, or
  a cut at a pause after 40 characters) took first audio from 1085 to 956 ms.

## 2026-11-14 (late evening): first run from `main`

`sasha window` started from `main` on the laptop, voice on. A typed question on a `speak=1`
socket: first text 164 ms, first audio 1.4 s, 50 tokens/s, LLM fully on the GPU. Typed input,
so it does not include speech-to-text. Not measured with a real microphone.

## 2026-11-14 (night): first wake word numbers (synthetic, not real audio)

Real speech model on CPU, 135 synthetic utterances (five voices, three speeds), through the real
matcher. The variants list is empty until measured with the owner's voice.

| Case | Accepted | Notes |
| --- | --- | --- |
| Name plus command | 45 of 45 | |
| Mid-sentence, possessive and near-name forms | 0 of 75 | |
| Name alone | 8 of 15 | A half-second isolated word is the weak case; every misspelling had confidence at or under 0.50; deeper voices were worst |

Speech-to-text on CPU: median 1935 ms per segment. That is not the production path (the brain
uses the GPU, about 127 ms). Nothing has run on real audio, and the measurement plan in
`decisions.md` (false accepts over 8 h, accept rate at 1, 3 and 5 m) has not started.

## 2026-11-14 (night): Marathi model facts (as reported by web fetch summaries; not verified)

The research is saved locally. Marathi–English is postponed (see `decisions.md`).

- The open Marathi models for speech-to-text, translation and text-to-speech are MIT or Apache
  licensed, but gated on Hugging Face (terms must be accepted and a token is needed).
- One small text-to-speech candidate is non-commercial, and another's training data is share-alike.
- No published Marathi evaluation exists for the two local LLMs in use.
- No published results for Marathi–English code-mixed speech exist for any candidate.


## 2026-11-14 (night): timers bench (before the last change; final run pending)

62 phrasings, 3 runs each (186 runs) through the real local model, on the server branch at its
last report. Bar: "0 silent wrong times" (met). One more change is being made (an answer is also
read together with the previous message when the assistant's own reply was a question), and the
bench will be re-run.

| Outcome | Runs | Share |
| --- | --- | --- |
| Right | 117 | 62.9% |
| Asked correctly | 54 | 29.0% |
| Asked needlessly | 15 | 8.1% |
| Wrong (silent wrong time) | 0 | 0% |
| Right or correctly asked | 171 | 91.9% |

Without the server's check of the model's fields against the person's words, the model invented
am/pm or a delay in 7 of 144 runs (earlier run, before the check). Final numbers: pending the
re-run.

## 2026-11-14 (night): the assistant's own browser, measurements

- **Injection bench, real model:** 8 kinds of trap page, 24 of 24 on the final run, 0 leaks to the
  network. An early run did leak an email through a search; that is why the query leak check
  exists, and why the guards do not rely on the model.
- **Address guard:** measured on the real Chrome, every tested loopback, private, link-local,
  metadata and mapped form was refused, also from page sub-resources.
- **Live view, end to end on local test pages:** first frame 0.10 s after the tab appears, 4.6
  frames a second, Chrome about 305 MB, a browsing turn of two tool calls in 5.7 s.
- **Finding:** the chosen search engine served the headless browser a bot challenge. Not worked
  around.
- **Wake listener:** a test that failed now and then was a real ordering bug (the tray sign could
  say "closed" for up to 2 s while the capture ran). Intermittent failures are product bugs until
  shown otherwise.
- Not tested for real: the wake word on a real microphone, the browser on a public site, timers
  with real voice.
