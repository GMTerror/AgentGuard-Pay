"""Signed spending mandates (Ed25519). Verified once per session; nonce is single-use."""

import base64
import json
import time
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

CLOCK_SKEW_SECONDS = 30


class MandateError(Exception):
    pass


@dataclass(frozen=True)
class Mandate:
    user_id: str
    session_id: str
    nonce: str
    issued_at: int
    expires_at: int
    max_total: str
    currency: str
    allowed_merchants: tuple


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def sign_mandate(mandate: Mandate, private_key: Ed25519PrivateKey) -> dict:
    """Runs on the USER's side. The gateway never sees the private key."""
    body = asdict(mandate)
    body["allowed_merchants"] = list(mandate.allowed_merchants)
    sig = private_key.sign(_canonical(body))
    return {"mandate": body, "signature": base64.b64encode(sig).decode()}


class MandateVerifier:
    def __init__(self, public_keys: dict, clock=time.time):
        self._keys = public_keys
        self._used_nonces: set = set()
        self._clock = clock

    def verify(self, signed: dict, session_id: str) -> Mandate:
        try:
            body = signed["mandate"]
            sig = base64.b64decode(signed["signature"])
            key = self._keys.get(body["user_id"])
        except (KeyError, TypeError, ValueError):
            raise MandateError("malformed mandate")
        if key is None:
            raise MandateError("unknown user")
        try:
            key.verify(sig, _canonical(body))
        except InvalidSignature:
            raise MandateError("bad signature")

        try:
            body = dict(body)
            body["allowed_merchants"] = tuple(body["allowed_merchants"])
            m = Mandate(**body)
        except (KeyError, TypeError):
            raise MandateError("malformed mandate fields")

        now = self._clock()
        if m.expires_at <= now:
            raise MandateError("expired")
        if m.issued_at > now + CLOCK_SKEW_SECONDS:
            raise MandateError("issued in the future")
        if m.session_id != session_id:
            raise MandateError("session mismatch")
        if m.nonce in self._used_nonces:
            raise MandateError("replayed nonce")
        self._used_nonces.add(m.nonce)
        return m
