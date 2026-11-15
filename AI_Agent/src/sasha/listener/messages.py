"""How the listener reacts to each frame from the brain (contract v0.2).

Binary frames are reply speech (played, half-duplex). JSON frames: `tts_start`,
`tts_end`, `wake`, `confirm`, `confirm_result`, `armed_expired`, `pause_capture`,
`resume_capture`, `state` (the first one marks the brain online) and `error`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from sasha.listener.indicator.base import EarconKind
from sasha.listener.playback import Player

if TYPE_CHECKING:
    from sasha.listener.listener import Listener

log = logging.getLogger(__name__)

AFTER_SPEECH_S = 0.3  # half-duplex: the mic stays closed this long after playback


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


async def _on_wake(lst: Listener, msg: dict[str, Any]) -> None:
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
    await Player(lst.player.command).play(clip.pcm, clip.sample_rate)
