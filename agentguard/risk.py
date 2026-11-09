"""Cumulative session risk: r_t = gamma * r_{t-1} + S_turn + S_drift + S_sem.

S_turn  (0..1): prompt-injection phrases in untrusted text the agent was shown.
S_drift (0..1): pressure on the mandate boundary (large attempts, denials).
S_sem   (0..1): semantic drift, an item that does not match what the user asked for (semantic.py).
Rule-based on purpose (no model download). Thresholds are placeholders to tune
from your attack scenarios.
"""

import re

_PATTERNS = [
    re.compile(p)
    for p in (
        r"ignore (all |any )?(previous|prior|above)",
        r"disregard",
        r"system prompt",
        r"you (must|should) (now )?(pay|send|transfer|buy|purchase)",
        r"override",
        r"new instructions",
        r"api key|secret|password|token",
        r"do not (tell|inform|ask) the user",
        r"without (asking|confirmation|approval)",
        r"admin|developer mode",
    )
]


def turn_score(text: str) -> float:
    t = text.lower()
    hits = sum(1 for p in _PATTERNS if p.search(t))
    return min(1.0, 0.5 * hits)


def drift_score(attempted_ratio: float, denied: bool) -> float:
    """attempted_ratio = attempted order total / mandate limit."""
    s = max(0.0, attempted_ratio - 0.5) * 2
    if denied:
        s += 0.5
    return min(1.0, s)


class RiskEngine:
    def __init__(self, gamma: float = 0.8, warn: float = 1.0, block: float = 2.5):
        if not 0 < gamma <= 1:
            raise ValueError("gamma must be in (0, 1]")
        self._gamma, self._warn, self._block = gamma, warn, block
        self._r = 0.0

    @property
    def score(self) -> float:
        return self._r

    def level(self) -> str:
        if self._r >= self._block:
            return "block"
        if self._r >= self._warn:
            return "warn"
        return "ok"

    def update(
        self,
        untrusted_text: str = "",
        attempted_ratio: float = 0.0,
        denied: bool = False,
        semantic: float = 0.0,
    ) -> float:
        self._r = (
            self._gamma * self._r
            + turn_score(untrusted_text)
            + drift_score(attempted_ratio, denied)
            + semantic
        )
        return self._r
