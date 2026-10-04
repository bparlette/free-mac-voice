"""
integrations/shooter_game/intents.py
------------------------------------
Deterministic, zero-latency mapping from a spoken phrase to Rogue Mech
Protocol actions. Pure functions so they are trivially unit-testable.

A single utterance can hold several commands ("kick kick kick",
"vent one vent two red") — map_voice_to_actions() returns ALL of them, in
spoken order, repeats included.

Actions (exact WebSocket protocol consumed by game.html):
    kick, shoot, vent_1, vent_2, vent_3, red, blue, start, select_0..select_7
"""

import re
from typing import List, Optional

MAX_ACTIONS_PER_UTTERANCE = 8

_NUM = {
    "1": 1, "one": 1, "won": 1,
    "2": 2, "two": 2, "to": 2, "too": 2,
    "3": 3, "three": 3, "tree": 3,
}

_PILOT_MAP = {
    "cap": 0, "captain": 0,
    "blaze": 1,
    "hook": 2,
    "sarge": 3, "sergeant": 3,
    "patch": 4, "patches": 4,
    "sumo": 5,
    "zen": 6,
    "marshal": 7, "marshall": 7,
}
_PILOT_NAMES = "|".join(sorted(_PILOT_MAP, key=len, reverse=True))

# Words that on their own are half a command ("vent" ... "one"): the bridge
# holds them briefly and joins them with the next utterance.
PREFIX_WORDS = {"vent", "cool", "core", "reactor", "pilot", "select", "choose", "pick", "number"}

# (regex, action-builder). All matches are collected, overlaps resolved
# left-to-right (earliest, then longest wins).
_PATTERNS = [
    (re.compile(r"\b(?:vent|cool|core|reactor)\s*(?:number\s*)?(1|one|won|2|two|to|too|3|three|tree)\b"),
     lambda m: f"vent_{_NUM[m.group(1)]}"),
    (re.compile(r"\b(?:select|choose|pick|pilot)\s+(" + _PILOT_NAMES + r")\b"),
     lambda m: f"select_{_PILOT_MAP[m.group(1)]}"),
    (re.compile(r"\b(?:select|choose|pick|pilot)\s*(?:number\s*)?([1-8])\b"),
     lambda m: f"select_{int(m.group(1)) - 1}"),
    (re.compile(r"\b(?:select|choose|pick|pilot)\s*(?:number\s*)?(one|two|three|four|five|six|seven|eight)\b"),
     lambda m: "select_" + str(["one", "two", "three", "four", "five", "six", "seven", "eight"].index(m.group(1)))),
    (re.compile(r"\b(" + _PILOT_NAMES + r")\b"),
     lambda m: f"select_{_PILOT_MAP[m.group(1)]}"),
    (re.compile(r"\b(?:red|read|rad)\b"), lambda m: "red"),
    (re.compile(r"\b(?:blue|blew|blu)\b"), lambda m: "blue"),
    (re.compile(r"\b(?:kick|kicks|kicked|kicking|kik|stomp|tank)\b"), lambda m: "kick"),
    (re.compile(r"\b(?:shoot|shoots|shot|shooting|fire|blast|rocket|jet)\b"), lambda m: "shoot"),
    (re.compile(r"\b(?:deploy|deployed|start|launch|restart|retry|again|play)\b"), lambda m: "start"),
]


def _normalize(phrase: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (phrase or "").lower()).strip()


def map_voice_to_actions(phrase: str) -> List[str]:
    """Return every game action in the phrase, in spoken order (repeats kept)."""
    p = _normalize(phrase)
    if not p:
        return []
    hits = []  # (start, end, action)
    for rx, fn in _PATTERNS:
        for m in rx.finditer(p):
            hits.append((m.start(), m.end(), fn(m)))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, last_end = [], -1
    for start, end, action in hits:
        if start >= last_end:
            out.append(action)
            last_end = end
        if len(out) >= MAX_ACTIONS_PER_UTTERANCE:
            break
    return out


def map_voice_to_action(phrase: str) -> Optional[str]:
    """First game action in the phrase, or None if unrelated."""
    acts = map_voice_to_actions(phrase)
    return acts[0] if acts else None


def is_dangling_prefix(phrase: str) -> bool:
    """True if the phrase is only a command prefix ('vent', 'pilot') that
    needs its number/name from the next utterance."""
    p = _normalize(phrase)
    return bool(p) and p in PREFIX_WORDS
