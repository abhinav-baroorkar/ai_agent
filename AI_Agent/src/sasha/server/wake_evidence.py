"""Evidence that the wake phrase itself was heard, not just written by the decoder.

A wake segment is decoded with the phrase as a hint (stt_whisper `hotwords`). On a
long stretch of other people's speech the decoder can then write the phrase at the
start although nobody said it (false wakes, 2026-11-16). The segment's average
confidence hardly moves on a 6 s segment, so the gate looks at the phrase's own words:
the decoder's per-word probability for them. Numbers only leave this module.

That number is recorded, not judged: a pronunciation the decoder doesn't know scores
the name 0.05-0.3 although it was said (test_pronunciations, bench, 2026-11-16), so a
bar on it would refuse the owner. The judging is the no-hint recheck (`hear`, and
WakeGate.needs_recheck).

Measured (bench/wake_false_accepts.py, 2026-11-16): under the hint the segment's first
word always scores about 0.02, said or not (the prompt makes the decoder expect a
continuation), so it is left out when the phrase has other words; the name itself
scores 0.8-0.9 when it was said.
"""

from __future__ import annotations

import asyncio
import functools
import re
import time
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from sasha.pipeline.stt import Transcript, Word


@dataclass(frozen=True)
class PhraseEvidence:
    probability: float  # the lowest per-word probability among the words that count
    words: tuple[float, ...]  # each phrase word's probability, in order (for the bench)
    start_s: float  # where the phrase starts in the segment
    duration_s: float  # from its first word's start to its last word's end


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", text)]


def phrase_evidence(
    text: str, words: Sequence[Word], span: tuple[int, int] | None
) -> PhraseEvidence | None:
    """`span` is WakeMatcher.phrase_tokens(text): (tokens before, tokens in the phrase).
    None when there are no words (a decoder without them) or they don't line up with
    the text; the caller decides what that means."""
    if span is None or not words:
        return None
    before, count = span
    owner: list[int] = []  # for each token of the words, the index of its word
    flat: list[str] = []
    for i, w in enumerate(words):
        for t in _tokens(w.text):
            flat.append(t)
            owner.append(i)
    needed = before + count
    if count < 1 or len(flat) < needed or flat[:needed] != _tokens(text)[:needed]:
        return None
    indexes = list(dict.fromkeys(owner[before:needed]))
    picked = [words[i] for i in indexes]
    counted = [words[i] for i in indexes if i != 0] or picked  # not the first
    return PhraseEvidence(
        probability=min(w.probability for w in counted),
        words=tuple(w.probability for w in picked),
        start_s=round(picked[0].start, 2),
        duration_s=round(picked[-1].end - picked[0].start, 2),
    )


async def hear(
    stt: Any,
    lock: AbstractAsyncContextManager[Any],
    audio: bytes,
    rate: int,
    hotwords: str | None,
    gate: Any,
    recheck: bool = True,
) -> tuple[Transcript, str | None, float]:
    """Decode a wake segment (with the hint); a doubtful one (`gate.needs_recheck`) again
    without it, opening words only, under the same lock. (transcript, no-hint text or
    None, start time). `recheck=False` for a stop-word segment: a ringing timer's stop
    must not wait for a second decode (review nit)."""
    seconds = len(audio) / (2 * rate)  # 16-bit mono
    async with lock:  # waits while another socket transcribes
        started = time.perf_counter()
        heard: Transcript = await asyncio.to_thread(
            functools.partial(stt.transcribe, hotwords=hotwords), audio, rate
        )
        if not hotwords:  # decoded without the hint already (e.g. the armed window ran
            return heard, heard.text, started  # out while waiting): it is the plain one
        plain = None
        if recheck and gate.needs_recheck(heard.text, heard.confidence, seconds):
            opening = functools.partial(stt.transcribe, opening=True)
            plain = (await asyncio.to_thread(opening, audio, rate)).text
    return heard, plain, started
