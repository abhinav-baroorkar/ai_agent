"""Build the speech stages from settings. The server calls these once at startup.

Both raise `VoiceUnavailableError` with a plain message when the `voice` extra or the
model files are missing, so the server can report `health.voice.stt/tts = false`
and keep text chat working.
"""

from __future__ import annotations

from sasha.config import Settings
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import TextToSpeech


class VoiceUnavailableError(RuntimeError):
    pass


def _missing_extra(e: ImportError) -> VoiceUnavailableError:
    return VoiceUnavailableError(
        f"Voice packages not installed ({e.name}). From AI_Agent/: uv sync --extra voice"
    )


def build_stt(settings: Settings) -> SpeechToText:
    cfg = settings.profile.stt
    if cfg.backend != "faster-whisper":
        raise VoiceUnavailableError(f"Unknown STT backend {cfg.backend!r}")
    try:
        from sasha.pipeline.stt_whisper import FasterWhisperSTT

        return FasterWhisperSTT(
            cfg, root=settings.paths.root, languages=settings.assistant.languages.supported
        )
    except ImportError as e:
        raise _missing_extra(e) from e
    except Exception as e:
        raise VoiceUnavailableError(f"STT failed to load: {e}") from e


def build_tts(settings: Settings) -> TextToSpeech:
    cfg = settings.profile.tts
    if cfg.backend != "kokoro":
        raise VoiceUnavailableError(f"Unknown TTS backend {cfg.backend!r}")
    try:
        from sasha.pipeline.tts_kokoro import KokoroTTS

        return KokoroTTS(cfg, voice=settings.assistant.voice, root=settings.paths.root)
    except ImportError as e:
        raise _missing_extra(e) from e
    except Exception as e:
        raise VoiceUnavailableError(f"TTS failed to load: {e}") from e
