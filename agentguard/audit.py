"""Append-only JSONL audit log. Each entry hashes the previous one (tamper-evident)."""

import hashlib
import json
import threading
import time
from decimal import Decimal
from enum import Enum

GENESIS = "0" * 64


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not serializable: {type(o)}")


def _canonical(d: dict) -> bytes:
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), default=_default
    ).encode()


def _hash(entry: dict) -> str:
    return hashlib.sha256(_canonical(entry)).hexdigest()


class AuditLog:
    def __init__(self, path: str, clock=time.time):
        self._path = path
        self._clock = clock
        self._prev = GENESIS
        self._lock = threading.Lock()
        try:
            with open(path) as f:
                for line in f:
                    if line.strip():
                        self._prev = json.loads(line)["hash"]
        except FileNotFoundError:
            pass

    def log(self, event: str, **data) -> None:
        with self._lock:
            entry = {
                "ts": self._clock(),
                "event": event,
                "data": data,
                "prev": self._prev,
            }
            entry["hash"] = _hash(entry)
            with open(self._path, "a") as f:
                f.write(_canonical(entry).decode() + "\n")
            self._prev = entry["hash"]

    def log_decision(self, action: str, decision, **ctx) -> None:
        """action: e.g. 'create_order', 'capture'. decision: a policy.Decision."""
        self.log(
            "decision",
            action=action,
            verdict=decision.verdict,
            reason=decision.reason,
            total=decision.total,
            **ctx,
        )


def verify_log(path: str) -> bool:
    """True if every entry's hash and prev-link are intact."""
    prev = GENESIS
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            claimed = entry.pop("hash")
            if entry["prev"] != prev or _hash(entry) != claimed:
                return False
            prev = claimed
    return True
