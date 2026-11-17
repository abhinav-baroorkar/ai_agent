"""The Wikipedia lookup's settings (browser/wikipedia.py; owner's decision 2026-11-16).

Wikimedia's User-Agent policy wants a descriptive agent with contact details (a URL is
enough) and the word "bot"; a generic or missing one may be refused without notice
and gets the lowest rate-limit tier. The contact here is the project's public repo:
no personal contact. The page's language is assistant.yaml's default language.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

REPO_URL = "https://github.com/abhinav-baroorkar/ai_agent"


class WikipediaConfig(BaseModel):
    user_agent: str = f"ai_agent-lookup-bot/0.1 ({REPO_URL})"
    timeout_s: float = Field(default=6.0, gt=0, le=30)  # each of the two requests
    extract_chars: int = Field(default=1200, ge=100, le=4000)
