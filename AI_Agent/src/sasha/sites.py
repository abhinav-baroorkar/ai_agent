"""Sites a turn's web results came from: the server's own `site`/`sites` fields of
tainting tools (browse: the tabs' record, P4), never the model's words."""

from __future__ import annotations

import json
import re

_HOST = re.compile(r"^[a-z0-9.-]{1,255}$")
MAX_REPLY_SITES = 5


def add_sites(sites: list[str], content: str) -> None:
    try:
        data = json.loads(content)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    listed = data.get("sites")
    for host in [data.get("site"), *(listed if isinstance(listed, list) else [])]:
        new = isinstance(host, str) and _HOST.match(host) and host not in sites
        if new and len(sites) < MAX_REPLY_SITES:
            sites.append(str(host))
