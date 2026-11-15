"""Search-engine redirect links are seen through (DuckDuckGo's `/l/?uddg=`)."""

from __future__ import annotations

from urllib.parse import quote

from sasha.browser.reader import read_tree
from sasha.browser.redirects import short, unwrap
from sasha.connectors.browser import DEFAULT_SEARCH, BrowserConnector, check_template

ENGINE = "html.duckduckgo.com"


def _ddg(target: str) -> str:
    return f"//duckduckgo.com/l/?uddg={quote(target, safe='')}&rut=abc"


def test_unwraps_only_the_engines_own_redirect() -> None:
    base = "https://html.duckduckgo.com/html/?q=x"
    assert unwrap(_ddg("https://example.org/a?b=1"), base, ENGINE) == "https://example.org/a?b=1"
    # private destinations are unwrapped too: the proxy refuses them at the final hop
    assert unwrap(_ddg("http://127.0.0.1:8765/"), base, ENGINE) == "http://127.0.0.1:8765/"
    for scheme in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"):
        assert unwrap(_ddg(scheme), base, ENGINE).startswith("https://duckduckgo.com/l/")
    other = "https://evil.example/l/?uddg=https%3A%2F%2Fexample.org"
    assert unwrap(other, base, ENGINE) == other  # not the engine's site
    assert unwrap("/l/?uddg=https%3A%2F%2Fx.org", base, None).startswith("https://html.")
    assert unwrap("/about", base, ENGINE) == "https://html.duckduckgo.com/about"


def test_short_shows_host_and_path_only() -> None:
    assert short("https://example.org/a/b?token=secret#x") == "example.org/a/b"
    assert short("https://example.org/") == "example.org"
    assert len(short("https://example.org/" + "a" * 200)) == 80


def test_reader_shows_the_destination() -> None:
    nodes = [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "childIds": ["2"]},
        {
            "nodeId": "2",
            "role": {"value": "link"},
            "name": {"value": "Result"},
            "backendDOMNodeId": 7,
        },
    ]
    view = read_tree(nodes, destinations={7: "https://example.org/page?x=1"})
    assert '"Result" → example.org/page' in view.part(1) and "x=1" not in view.part(1)


def test_default_search_is_duckduckgo_html() -> None:
    assert DEFAULT_SEARCH == "https://html.duckduckgo.com/html/?q={q}"
    assert check_template(DEFAULT_SEARCH) == DEFAULT_SEARCH
    assert BrowserConnector.__new__(BrowserConnector).default_settings() == {
        "search_template": DEFAULT_SEARCH
    }
