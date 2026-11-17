"""The Wikipedia lookup's settings (browser/wikipedia.py), in a profile under `wikipedia:`.

`user_agent` is how the assistant names itself to Wikipedia: the User-Agent header
sent with every lookup.
- What Wikimedia's policy asks for: a name for the program, the word "bot", and a way
  to reach whoever runs it (a web address or an email), for example
  `my-assistant-bot/1.0 (https://example.org/my-assistant)`. A request with a missing
  or generic agent may be refused without notice, and gets the lowest rate limit.
- Why it is empty by default: whatever address goes here is sent to Wikimedia with
  every lookup, next to the computer's IP address. So it ties the lookups to whoever
  that address names. Privacy comes first, so the owner chooses it himself and writes
  it into his own profile.
- While it is empty, or names no web address or email, the assistant sends nothing:
  the lookup says, in text and aloud, that it isn't set up.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WikipediaConfig(BaseModel):
    user_agent: str = ""  # the owner's choice (see above); empty: no lookups
    timeout_s: float = Field(default=6.0, gt=0, le=30)  # each of the two requests
    extract_chars: int = Field(default=1200, ge=100, le=4000)
