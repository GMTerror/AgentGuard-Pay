import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentguard.mandate import Mandate, MandateError, MandateVerifier, sign_mandate

NOW = 1_000_000


def make(**kw):
    base = dict(
        user_id="u1",
        session_id="s1",
        nonce="n1",
        issued_at=NOW,
        expires_at=NOW + 600,
        max_total="100.00",
        currency="USD",
        allowed_merchants=("shop-a",),
    )
    base.update(kw)
    return Mandate(**base)


@pytest.fixture
def setup():
    priv = Ed25519PrivateKey.generate()
    ver = MandateVerifier({"u1": priv.public_key()}, clock=lambda: NOW)
    return priv, ver


def test_valid(setup):
    priv, ver = setup
    assert ver.verify(sign_mandate(make(), priv), "s1").max_total == "100.00"


def test_tampered_limit(setup):
    priv, ver = setup
    signed = sign_mandate(make(), priv)
    signed["mandate"]["max_total"] = "9999.00"
    with pytest.raises(MandateError, match="bad signature"):
        ver.verify(signed, "s1")


def test_wrong_key(setup):
    _, ver = setup
    with pytest.raises(MandateError, match="bad signature"):
        ver.verify(sign_mandate(make(), Ed25519PrivateKey.generate()), "s1")


def test_unknown_user(setup):
    priv, ver = setup
    with pytest.raises(MandateError, match="unknown user"):
        ver.verify(sign_mandate(make(user_id="evil"), priv), "s1")


def test_expired(setup):
    priv, ver = setup
    with pytest.raises(MandateError, match="expired"):
        ver.verify(sign_mandate(make(expires_at=NOW - 1), priv), "s1")


def test_future_issued(setup):
    priv, ver = setup
    with pytest.raises(MandateError, match="future"):
        ver.verify(sign_mandate(make(issued_at=NOW + 3600), priv), "s1")


def test_session_mismatch(setup):
    priv, ver = setup
    with pytest.raises(MandateError, match="session"):
        ver.verify(sign_mandate(make(), priv), "other")


def test_replay(setup):
    priv, ver = setup
    signed = sign_mandate(make(), priv)
    ver.verify(signed, "s1")
    with pytest.raises(MandateError, match="replayed"):
        ver.verify(signed, "s1")


def test_malformed(setup):
    _, ver = setup
    with pytest.raises(MandateError):
        ver.verify({"nope": 1}, "s1")
