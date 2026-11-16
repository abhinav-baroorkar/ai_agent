"""Opening the listener socket's conversation (`/ws?wake=1`): the `voice` bot wrapped in
the wake-turn policy. The conversation itself is server/wake_session.py."""

from __future__ import annotations

import asyncio
import logging

from starlette.websockets import WebSocket

from sasha.bots import VOICE_ID
from sasha.prompting import prompt_lines
from sasha.server.deps import ServerDeps
from sasha.server.timers.hub import Hub
from sasha.server.voice import VoiceStages
from sasha.server.wake import WakeGate, WakeMatcher
from sasha.server.wake_policy import CONTROL_TOOL
from sasha.server.wake_session import ListenerConversation
from sasha.server.wake_truth import wake_control_allowed

log = logging.getLogger(__name__)


async def open_listener(
    ws: WebSocket, deps: ServerDeps, voice: VoiceStages, speak: bool, timers: Hub | None = None
) -> ListenerConversation:
    """The listener's conversation: the `voice` bot, wrapped in the wake-turn policy."""
    settings = deps.settings
    bot = deps.bots.get(VOICE_ID)
    assistant, warnings = await asyncio.to_thread(deps.make_assistant, bot)
    for warning in warnings:
        log.warning("Bot %s: %s", bot.id, warning)
    wake_word, config = settings.assistant.wake_word, settings.profile.wake
    matcher = WakeMatcher(
        wake_word.phrase,
        wake_word.variants,
        config.fillers,
        config.continuations,
        wake_word.word_variants,
    )
    assert deps.wake_limiter is not None
    conv = ListenerConversation(
        ws,
        assistant,
        voice,
        language=settings.assistant.languages.default,
        config=config,
        gate=WakeGate(matcher, config.armed_window_s, config.min_confidence),
        limiter=deps.wake_limiter,
        # Checked on every call: the opt-in, and truthful replies still on (the gate).
        device_control=lambda: (
            deps.bots.get(VOICE_ID).wake_device_control and wake_control_allowed(config)
        ),
        telemetry=deps.telemetry,
        presence=deps.presence,
        recheck_placement=deps.recheck_placement,
        speak=speak,
        timers=timers,
    )

    def wake_prompt() -> str:
        """What wake turns can really do now: the policy's view of the tools (the wake
        timer tools included), without device control unless it is allowed now; then
        the wake turn's own line (a hint; TruthfulReply is the guarantee). Rebuilt every
        turn, so turning the opt-in on or off takes effect at once."""
        control = conv.policy.device_control()
        names = [n for n in conv.assistant.tools.names if n != CONTROL_TOOL or control]
        prompt = deps.prompt_for(deps.bots.get(VOICE_ID), names, spoken=speak)
        return f"{prompt}\n{config.prompt_note}"

    conv.wake_prompt = wake_prompt
    assistant.system_prompt = wake_prompt()
    conv.times_word = prompt_lines(settings).times_word
    return conv
