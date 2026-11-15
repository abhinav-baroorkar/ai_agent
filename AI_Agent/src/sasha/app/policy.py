"""What the app shell lets its page do: pure rules, no Qt (tested without PySide6).

- Requests: only the brain's own address (`http://` and `ws://` on 127.0.0.1 and the
  brain's port), plus `data:` and `blob:`. Everything else is blocked, whatever the page
  asks for (design 2).
- Navigation: only within the brain's own address; a link elsewhere opens nothing.
- Permissions: the microphone, for the brain's own origin only (design 4). Everything
  else is denied.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOCAL_HOST = "127.0.0.1"
INTERNAL_SCHEMES = frozenset({"data", "blob"})
MICROPHONE = "MediaAudioCapture"


def own_origin(port: int) -> str:
    return f"http://{LOCAL_HOST}:{port}"


def request_allowed(url: str, port: int) -> bool:
    try:
        parts = urlsplit(url)
        url_port = parts.port
    except ValueError:  # e.g. an out-of-range port
        return False
    if parts.scheme in INTERNAL_SCHEMES:
        return True
    if parts.scheme not in ("http", "ws"):  # no https/wss: nothing of ours uses them
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return parts.hostname == LOCAL_HOST and url_port == port


def navigation_allowed(url: str, port: int) -> bool:
    try:
        parts = urlsplit(url)
        url_port = parts.port
    except ValueError:
        return False
    if parts.scheme == "about":
        return parts.path == "blank"
    return parts.scheme == "http" and parts.hostname == LOCAL_HOST and url_port == port


def permission_allowed(origin: str, kind: str, port: int) -> bool:
    return kind == MICROPHONE and origin.rstrip("/") == own_origin(port)
