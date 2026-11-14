"""Web face driven over WebSocket (Phase 4). States: idle, listening, thinking, speaking."""

from enum import Enum


class AvatarState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
