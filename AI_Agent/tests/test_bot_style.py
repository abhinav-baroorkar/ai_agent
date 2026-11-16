"""Bot.style: how a bot's orb breathes (owner-approved, feat/bot-style-field)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sasha.bots import BOT_STYLES, DEFAULT_STYLE
from sasha.config import Settings
from tests.server_fakes import Harness


@pytest.fixture
def h(settings: Settings, tmp_path: Path) -> Harness:
    return Harness(settings, tmp_path)


@pytest.fixture
def client(h: Harness) -> TestClient:
    return h.client()


def bots(client: TestClient) -> dict[str, dict[str, object]]:
    return {b["id"]: b for b in client.get("/api/bots").json()["bots"]}


def test_the_styles_are_one_list_and_glow_is_the_default(client: TestClient) -> None:
    assert BOT_STYLES == ("glow", "ember", "tide", "gale", "storm", "mist", "bloom", "stone")
    assert DEFAULT_STYLE == "glow"
    listed = bots(client)
    assert listed["main"]["style"] == "glow" and listed["voice"]["style"] == "glow"
    made = client.post("/api/bots", json={"name": "Chef"}).json()
    assert made["style"] == "glow"


def test_user_bots_take_a_style_on_create_and_patch(client: TestClient) -> None:
    made = client.post("/api/bots", json={"name": "Chef", "style": "ember"}).json()
    assert made["style"] == "ember" and bots(client)[made["id"]]["style"] == "ember"
    r = client.patch(f"/api/bots/{made['id']}", json={"style": "tide"})
    assert r.status_code == 200 and r.json()["style"] == "tide"
    assert bots(client)[made["id"]]["style"] == "tide"


def test_main_takes_only_its_style_and_the_voice_bot_shows_it(client: TestClient) -> None:
    r = client.patch("/api/bots/main", json={"style": "storm"})
    assert r.status_code == 200 and r.json()["style"] == "storm"
    listed = bots(client)
    assert listed["main"]["style"] == "storm" and listed["voice"]["style"] == "storm"
    locked = client.patch("/api/bots/main", json={"style": "mist", "name": "X"})
    assert locked.status_code == 400 and bots(client)["main"]["style"] == "storm"
    own = client.patch("/api/bots/voice", json={"style": "bloom"})
    assert own.status_code == 400 and "main bot's style" in own.json()["error"]
    assert bots(client)["voice"]["style"] == "storm"


@pytest.mark.parametrize("bad", ["neon", "", "GLOW", 3])
def test_an_unknown_style_is_a_plain_400(client: TestClient, bad: object) -> None:
    for r in (
        client.post("/api/bots", json={"name": "Chef", "style": bad}),
        client.patch("/api/bots/main", json={"style": bad}),
    ):
        assert r.status_code == 400 and "style" in r.json()["error"]
    assert "glow" in client.post("/api/bots", json={"name": "C", "style": bad}).json()["error"]


def test_null_style_means_default_on_create_and_is_refused_on_patch(client: TestClient) -> None:
    made = client.post("/api/bots", json={"name": "Chef", "style": None})
    assert made.status_code == 201 and made.json()["style"] == "glow"
    assert client.patch(f"/api/bots/{made.json()['id']}", json={"style": None}).status_code == 400


def test_state_files_without_a_style_or_with_a_bad_one_load_as_glow(h: Harness) -> None:
    state = h.settings.paths.state_dir / "bots.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "main": {"model": "big-model"},
                "bots": [
                    {"id": "old-1", "name": "Old", "model": "m", "system_prompt": "",
                     "connectors": []},
                    {"id": "odd-1", "name": "Odd", "model": "m", "system_prompt": "",
                     "connectors": [], "style": "neon"},
                ],
            }
        )
    )  # fmt: skip
    listed = bots(h.client())
    assert listed["main"]["style"] == "glow"
    assert listed["old-1"]["style"] == "glow" and listed["odd-1"]["style"] == "glow"
