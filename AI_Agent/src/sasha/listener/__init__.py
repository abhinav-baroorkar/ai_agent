"""Always-on wake listener (`sasha listen`): the first satellite of the brain.

Microphone (a capture child process) -> local gate (Silero VAD + loudness) -> speech
segments over the brain's WebSocket. Fail closed: the microphone is open only while
every guard (indicator shown, screen unlocked, session active, not suspending, not
paused, not on battery when that pause is on, brain online) says yes.
The listener writes no files, except the CLI-only debug audio dump.
"""
