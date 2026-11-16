"""Did the owner say this change in his own words, this turn? (review P1, memory M1)

While outside text is in the conversation (a `browse` result), a changing tool runs
only if the owner's current message names it:
- the target: a distinctive word of the device's real name (from Home Assistant's own
  list, never the model's text), or of its entity id; for `entity_id="all"` the kind
  of device ("lights");
- an action word that matches the service: on/off, lock/unlock, play/pause, volume,
  set/dim for a brightness, or the scene's own name for a scene.
Anything else, including a service this table doesn't know and every changing tool
other than `call_service`, is refused: it fails closed. One check, meant to be shared
by the memory rule (M1) and, once timers land, the timers' own said-check.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from typing import Any

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)  # keeps "don't" whole
DEVICE_TOOL = "call_service"
# Review G1: a negation within this many words before a verb or an action word refuses
# the change ("don't turn off the kitchen light"). Fails closed ("no, turn it off" is
# refused too). The words are per language (profile.prompt_lines.negations).
NEGATION_REACH = 3
NEGATIONS = ("don't", "dont", "do not", "never", "not", "no", "stop", "doesn't", "won't",
             "shouldn't", "mustn't", "can't", "cannot")  # fmt: skip
VERBS = {"turn", "switch", "set", "put", "make", "lock", "unlock", "open", "close", "play",
         "pause", "dim", "brighten", "raise", "lower", "start", "run", "activate", "mute",
         "toggle", "skip", "change", "resume"}  # fmt: skip

# The words that name a kind of device (also the generic part of a device's name).
KIND_WORDS: dict[str, set[str]] = {
    "light": {"light", "lights", "lamp", "lamps", "bulb", "bulbs"},
    "switch": {"switch", "switches", "plug", "plugs", "socket", "sockets"},
    "fan": {"fan", "fans"},
    "lock": {"lock", "locks", "door", "doors"},
    "media_player": {"tv", "television", "music", "speaker", "speakers", "player", "media"},
    "cover": {"blind", "blinds", "curtain", "curtains", "shutter", "shutters", "garage"},
    "climate": {"thermostat", "heating", "heater", "ac", "aircon", "temperature"},
    "scene": {"scene", "scenes"},
}
ACTION_WORDS: dict[str, set[str]] = {
    "turn_on": {"on", "start"},
    "turn_off": {"off", "stop"},
    "toggle": {"toggle", "flip", "switch"},
    "lock": {"lock"},
    "unlock": {"unlock"},
    "open_cover": {"open", "raise"},
    "close_cover": {"close", "lower"},
    "media_play": {"play", "resume"},
    "media_pause": {"pause"},
    "media_stop": {"stop"},
    "volume_set": {"volume"},
    "volume_up": {"volume", "louder", "up"},
    "volume_down": {"volume", "quieter", "down"},
    "volume_mute": {"mute", "unmute"},
    "set_temperature": {"temperature", "degrees", "set"},
}
SETTING_WORDS = {"set", "dim", "brighten", "brightness", "percent", "colour", "color", "warmer"}
SETTING_KEYS = {"brightness", "brightness_pct", "color_name", "color_temp", "kelvin", "rgb_color"}
FILLER = {"the", "a", "an", "my", "of", "in", "on", "and", "all", "main", "room"}


def raw_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def words(text: str) -> set[str]:
    """Lowercase words, plus each plural's singular ("lights" also gives "light")."""
    found = raw_words(text)
    return found | {w[:-1] for w in found if w.endswith("s") and len(w) > 3}


# Words that carry no meaning of their own, allowed in a free-text field the owner's
# words must cover ("buy the milk" for "remind me to buy milk").
GLUE = {"a", "an", "the", "to", "my", "of", "for", "and", "at", "on", "in"}


# Review nit: a leading "no," or "no." is a correction ("no, turn off the kitchen
# light"), not a negation of what follows.
_LEADING_NO = re.compile(r"^\s*no\s*[,.!;:]\s*", re.IGNORECASE)
# Review S1: these before a target leave it out ("..., not the bedroom").
EXCLUSIONS = {"except", "excluding", "without", "but"}
EXCLUSION_REACH = 2


def _tokens(text: str) -> list[str]:
    plain = _LEADING_NO.sub("", text.replace("\u2019", "'"))
    return [t.lower() for t in _TOKEN.findall(plain)]


def _negation_hits(tokens: list[str], i: int, reach: int, negations: tuple[str, ...]) -> bool:
    before = tokens[max(0, i - reach) : i]
    single = {n for n in negations if " " not in n}
    pairs = {tuple(n.split()) for n in negations if " " in n}
    if single & set(before):
        return True
    return any(tuple(before[j : j + 2]) in pairs for j in range(len(before) - 1))


def negation_count(text: str, negations: tuple[str, ...] = NEGATIONS) -> int:
    """How many negations a text holds ("do not" counts once)."""
    tokens = _tokens(text)
    single = {n for n in negations if " " not in n}
    pairs = [tuple(n.split()) for n in negations if " " in n]
    count = sum(t in single for t in tokens)
    return count + sum(tuple(tokens[i : i + 2]) in pairs for i in range(len(tokens) - 1))


def said_by_owner(field: str, owner_text: str, negation_text: str | None = None) -> bool:
    """Every word of a free-text field (a reminder's text, a label) is the owner's own,
    or glue; a plural of his word counts. An empty field passes. Review S2: the field
    keeps exactly the owner's negations (`negation_text`, default `owner_text`), so
    "call the bank" never stands for "remind me to not call the bank"."""
    if not field.strip():
        return True
    allowed = words(owner_text) | GLUE
    for word in raw_words(field.replace("\u2019", "'")):
        if word not in allowed and not (word.endswith("s") and {word[:-1], word[:-2]} & allowed):
            return False
    source = owner_text if negation_text is None else negation_text
    return negation_count(field) == negation_count(source)


def negated(owner_text: str, action_words: set[str], negations: tuple[str, ...]) -> bool:
    """A negation within `NEGATION_REACH` words before a verb or an action word."""
    tokens = _tokens(owner_text)
    for i, token in enumerate(tokens):
        if token not in VERBS and token not in action_words:
            continue
        if _negation_hits(tokens, i, NEGATION_REACH, negations):
            return True
    return False


def named_words(text: str, negations: tuple[str, ...] = NEGATIONS) -> set[str]:
    """Review S1: the words of `text` (and plurals' singulars) that occur at least once
    not left out by a negation or "except" within 2 words before them: "turn the kitchen
    light off, not the bedroom" names the kitchen, not the bedroom."""
    tokens = _tokens(text)
    leave_out = (*negations, *EXCLUSIONS)
    found: set[str] = set()
    for i, token in enumerate(tokens):
        if not _negation_hits(tokens, i, EXCLUSION_REACH, leave_out):
            found.add(token)
    return found | {w[:-1] for w in found if w.endswith("s") and len(w) > 3}


# Review E2: for these the target must be in the current message ("unlock it" never
# carries a door over from an earlier question).
NO_CARRY_OVER = {"lock", "alarm_control_panel", "cover"}


def names_a_target(said: set[str], names: Mapping[str, str]) -> bool:
    """Review E1: does the current message name a device of its own (a word of any real
    device name, or a kind of device)? Then the earlier messages fill in nothing."""
    actions = set().union(*ACTION_WORDS.values())
    kinds = set().union(*KIND_WORDS.values()) - VERBS - actions  # "switch it off": a verb
    if said & kinds:
        return True
    for entity, name in names.items():
        domain = entity.split(".", 1)[0]
        if said & _target_words(domain, entity, name):
            return True
    return False


def _only_one_of_its_kind(domain: str, entity: str, names: Mapping[str, str]) -> bool:
    """Review G2: the house's only device of that kind may be named by the kind ("the
    tv"); with several, the kind word alone never names one (fails closed)."""
    same = [e for e in names if e.startswith(f"{domain}.")]
    return same == [entity]


def _target_words(domain: str, entity: str, name: str | None) -> set[str]:
    """The distinctive words that name this device (not "light", not "the")."""
    object_id = entity.split(".", 1)[1] if "." in entity else entity
    generic = KIND_WORDS.get(domain, set()) | FILLER | {domain}
    named = words(name or "") | words(object_id.replace("_", " "))
    return {w for w in named if w not in generic and not w.isdigit()}


def _entities(entity_id: Any) -> list[str]:
    if isinstance(entity_id, str):
        return [entity_id]
    if isinstance(entity_id, list):
        return [str(e) for e in entity_id]
    return []


def device_change_said(
    owner_text: str,
    arguments: Mapping[str, Any],
    names: Mapping[str, str],
    earlier: str = "",
    negations: tuple[str, ...] = NEGATIONS,
) -> bool:
    """`call_service(domain, service, entity_id, data)` named in `owner_text`? The target
    may also come from `earlier` (the owner's own recent messages, built by the
    orchestrator: "is the bedroom light on?" then "turn it off"); the action only ever
    from `owner_text`."""
    said = words(owner_text)
    domain, service = str(arguments.get("domain", "")), str(arguments.get("service", ""))
    targets = named_words(owner_text, negations)  # S1: a left-out device doesn't count
    if earlier and domain not in NO_CARRY_OVER and not names_a_target(said, names):
        targets |= named_words(earlier, negations)  # "turn it off": the one he asked about
    entities = _entities(arguments.get("entity_id"))
    if not entities:
        return False
    for entity in entities:
        if entity == "all":
            if domain == "scene" or not targets & KIND_WORDS.get(domain, {domain}):
                return False
            continue
        target = _target_words(domain, entity, names.get(entity))
        if _only_one_of_its_kind(domain, entity, names):
            target |= KIND_WORDS.get(domain, set())
        if not target or not targets & target:
            return False
    scene_words = [_target_words(domain, e, names.get(e)) for e in entities]
    actions = ACTION_WORDS.get(service, set()) | SETTING_WORDS
    if domain == "scene":
        actions = actions | set().union(*scene_words)
    if negated(owner_text, actions, negations):
        return False  # review G1: "don't turn off the kitchen light"
    if domain == "scene":  # the scene's own name is the action: said this turn
        return service == "turn_on" and all(said & w for w in scene_words)
    data = arguments.get("data")
    sets_value = isinstance(data, dict) and bool(SETTING_KEYS & set(data))
    if service == "turn_on" and sets_value and (said & SETTING_WORDS or "%" in owner_text):
        return True
    return bool(said & ACTION_WORDS.get(service, set()))


class SaidGuard:
    """The orchestrator's `ChangeGuard` for one conversation: device changes only, with
    the real device names from `list_devices` (cached; re-read for an unknown id)."""

    def __init__(
        self, list_devices: Callable[..., Any] | None, negations: tuple[str, ...] = NEGATIONS
    ) -> None:
        self.list_devices = list_devices
        self.negations = negations
        self.names: dict[str, str] = {}
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        if self.list_devices is None:
            return
        try:
            devices = self.list_devices()
        except Exception:  # can't read the names: the id's own words still count
            return
        with self._lock:
            for device in devices if isinstance(devices, list) else []:
                if isinstance(device, dict) and isinstance(device.get("entity_id"), str):
                    self.names[device["entity_id"]] = str(device.get("name") or "")[:60]

    def __call__(
        self, tool: str, arguments: dict[str, Any], owner_text: str, earlier: str = ""
    ) -> bool:
        if tool != DEVICE_TOOL:
            return False  # fails closed: only device changes can be checked
        if any(e not in self.names for e in _entities(arguments.get("entity_id")) if e != "all"):
            self._refresh()
        return device_change_said(owner_text, arguments, self.names, earlier, self.negations)
