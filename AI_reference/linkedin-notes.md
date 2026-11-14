# LinkedIn notes

One section per phase: a short post draft, a demo shot list, and the one number
to share. Plain language, no hype.

## Phase 0: a local assistant, text only (not posted until the real-light gate passes)

**The one number:** 43 tokens/s on a local 9B model, and a tool call in 1.6 s,
on an 8 GB laptop GPU. No cloud.

**Post draft**

I'm building a home assistant that runs fully on my own hardware, in Marathi and
English. It's open source.
Phase 0 is almost done: a text chat where a local model calls Home Assistant
tools. Next: a real light.
On an 8 GB laptop GPU the model writes 43 tokens/s and makes a tool call in 1.6 s.
Voice, the face and Marathi come next. Code and notes are public.

**Demo shot list (20 to 40 s)**

Film shots 1 and 2 after the Docker gate passes; today `sasha doctor` prints
"[skip]" for Home Assistant and there is no fake Home Assistant in the CLI.

1. 0-5 s: terminal, `sasha doctor` showing Ollama and Home Assistant OK.
2. 5-20 s: type "turn on the bedroom light". Show the tool call and the reply
   with `--timing`, then cut to the real light turning on.
3. 20-30 s: show the telemetry JSON line (latency, tokens/s, VRAM).
4. 30-35 s: end card with the repo name and "Next: voice".
