"""
integrations/shooter_game/intents.py
------------------------------------
Deterministic, zero-latency mapping from a spoken phrase to a Rogue Mech
Protocol action. Pure function so it is trivially unit-testable.

Actions (exact WebSocket protocol consumed by game.html):
    kick, shoot, vent_1, vent_2, vent_3, red, blue, start
"""

import re
from typing import Optional

_NUM = {
    "1": 1, "one": 1, "won": 1,
    "2": 2, "two": 2, "to": 2, "too": 2,
    "3": 3, "three": 3, "tree": 3,
}

# (regex, action-or-callable). The EARLIEST match in the phrase wins so
# "red no blue" resolves to the first thing said.
_PATTERNS = [
    (re.compile(r"\b(?:vent|cool|core|reactor)\s*(?:number\s*)?(1|one|won|2|two|to|too|3|three|tree)\b"),
     lambda m: f"vent_{_NUM[m.group(1)]}"),
    (re.compile(r"\bred\b"), lambda m: "red"),
    (re.compile(r"\bblue\b"), lambda m: "blue"),
    (re.compile(r"\b(?:kick|stomp|tank)\b"), lambda m: "kick"),
    (re.compile(r"\b(?:shoot|fire|blast|jet)\b"), lambda m: "shoot"),
    (re.compile(r"\b(?:start|deploy|restart|retry|again)\b"), lambda m: "start"),
]


def map_voice_to_action(phrase: str) -> Optional[str]:
    """Return the game action for a spoken phrase, or None if unrelated."""
    p = re.sub(r"[^a-z0-9 ]+", " ", (phrase or "").lower()).strip()
    if not p:
        return None
    best = None
    for rx, fn in _PATTERNS:
        m = rx.search(p)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), fn(m))
    return best[1] if best else None
