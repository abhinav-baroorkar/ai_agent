"""How the listener reacts to each frame from the brain (contract v0.2).

Binary frames are reply speech (played, half-duplex). JSON frames: `tts_start`,
`tts_end`, `wake`, `confirm`, `confirm_result`, `armed_expired`, `pause_capture`,
`resume_capture`, `state` (the first one marks the brain online) and `error`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from sasha.listener.indicator.base import EarconKind
from sasha.listener.playback import Player

if TYPE_CHECKING:
    from sasha.listener.listener import Listener

log = logging.getLogger(__name__)

AFTER_SPEECH_S = 0.3  # half-duplex: the mic stays closed this long after playback
PRIME_S = 1.0  # how soon after the "armed" tone a reopen still counts as "right away"


async def handle_message(lst: Listener, message: str | bytes) -> None:
    if isinstance(message, bytes):
        await lst.player.write(message)
        return
    try:
        msg = json.loads(message)
    except ValueError:
        return
    kind = msg.get("type")
    if kind == "tts_start":
        lst.playing = True
        lst.confirm.speech_started()  # a window that opened early waits for this speech
        lst.stop_window.close()  # the next tone ends a stop window
        lst._changed()  # closes the mic before the first sound
        await lst.player.start(int(msg.get("sample_rate") or 24000))
    elif kind == "tts_end":
        await lst.player.finish()
        await asyncio.sleep(AFTER_SPEECH_S)
        lst.playing = False
        lst.confirm.speech_done()  # a pending confirmation opens its window now
        lst._changed()
    elif kind == "wake":
        await _on_wake(lst, msg)
    elif kind == "stop_window":
        lst.stop_window.open(msg.get("timeout_s") or 8.0)  # explicit event only
    elif kind in ("stop_result", "timer_stopped"):
        lst.stop_window.close()
    elif kind == "confirm":
        lst.confirm.announce(msg.get("timeout_s") or 5.0, lambda: lst.playing)
        lst._changed()
    elif kind == "confirm_result":
        lst.confirm.close()
    elif kind == "armed_expired":
        log.info("Armed window ended")
        lst._changed()
    elif kind in ("pause_capture", "resume_capture"):
        lst.server_paused = kind == "pause_capture"
        lst._changed()
    elif kind == "state":
        if not lst.online and lst._ws is not None:
            lst.online = True
            log.info("Connected to the brain")
        lst.server_state = str(msg.get("state"))
        if lst.server_state == "idle":
            lst.busy = False
            lst.confirm.close()
        lst._changed()
    elif kind == "error":
        log.warning("Brain error: %s", str(msg.get("message", ""))[:200])


# What the brain decided, in plain words, for the `sasha listen` terminal. Labels only:
# the brain never sends what was said, and nothing here is stored.
WHY = {
    "rejected_no_name": "the wake phrase wasn't heard",
    "rejected_low_conf": "the wake phrase was unclear",
    "rejected_mid_sentence": "the wake phrase wasn't at the start",
    "rejected_possessive": "the name was talked about",
    "rejected_continuation": "the name was talked about",
    "dropped_busy": "busy answering",
    "dropped_speaking": "it was speaking",
    "cooldown_drop": "it had just answered",
    "dropped_paused": "listening is paused",
    "stt_error": "speech recognition failed",
}


def wake_line(msg: dict[str, Any]) -> str:
    if msg.get("heard"):
        if msg.get("command"):
            return "[wake] heard the wake phrase and a command"
        return "[wake] heard the wake phrase; say the command"
    reason = str(msg.get("reason") or "")
    return f"[wake] not a wake: {WHY.get(reason, reason or 'unknown')}"


async def _on_wake(lst: Listener, msg: dict[str, Any]) -> None:
    print(wake_line(msg), flush=True)
    if not msg.get("heard"):
        return  # a miss: no sound, nothing kept
    if msg.get("command"):
        lst.busy = True
        lst._changed()  # the mic closes while the brain answers
        await _earcon(lst, "wake")
    else:
        await _earcon(lst, "armed")


async def _earcon(lst: Listener, kind: EarconKind) -> None:
    try:
        clip = lst.indicator.earcon(kind)
    except Exception as e:
        log.warning("Earcon %s unavailable (%s)", kind, type(e).__name__)
        return
    # Half-duplex for earcons too: the mic is closed while our own sound plays and
    # for the usual tail after it, so the tone is never sent as speech.
    lst.playing = True
    lst._changed()
    try:
        await Player(lst.player.command).play(clip.pcm, clip.sample_rate)
        await asyncio.sleep(AFTER_SPEECH_S)
    finally:
        lst.playing = False
        if kind == "armed":
            lst.prime_until = time.monotonic() + PRIME_S  # the command may follow at once
        lst._changed()
