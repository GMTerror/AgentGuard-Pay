"""Semantic intent drift: does what the agent is buying match what the USER asked for?

The user's own messages are the trusted record of intent (the agent can't write them).
Drift is 0 when an item resembles something the user asked for, rising to 1 as it stops.
It only feeds the risk score (escalating to user approval); the gate still decides.
"""

import math
import re
import zlib


def _cos(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class IntentTracker:
    def __init__(self, embedder, floor: float = 0.30):
        """embedder(list[str]) -> list of vectors. floor: similarity below this starts to count as drift."""
        self._embed, self._floor, self._vecs = embedder, floor, []

    def add(self, user_text: str) -> None:
        if user_text.strip():
            self._vecs.append(self._embed([user_text])[0])

    def similarity(self, text: str):
        if not self._vecs or not text.strip():
            return None
        v = self._embed([text])[0]
        return max(_cos(v, u) for u in self._vecs)

    def drift(self, text: str) -> float:
        sim = self.similarity(text)
        if sim is None or sim >= self._floor:
            return 0.0
        return min(
            1.0, (self._floor - sim) / (self._floor / 2)
        )


class MiniLM:
    """Real embedder. pip install sentence-transformers (first run downloads ~90 MB)."""

    def __init__(self, name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer(name)

    def __call__(self, texts):
        return [
            [float(x) for x in v]
            for v in self._m.encode(texts, normalize_embeddings=True)
        ]


_STOP = {
    "a",
    "an",
    "the",
    "me",
    "buy",
    "get",
    "please",
    "i",
    "want",
    "to",
    "for",
    "some",
    "under",
    "my",
}


class ToyEmbedder:
    """Word-overlap stand-in so offline demos and tests need no model download. Not for real use."""

    def __call__(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 256
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                if w not in _STOP:
                    v[zlib.crc32(w.encode()) % 256] += 1.0
            out.append(v)
        return out
