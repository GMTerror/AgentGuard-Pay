from decimal import Decimal
from demo.attacks import (
    malicious_catalog,
    multi_turn_drift,
    replay_mandate,
    semantic_drift,
)


def test_malicious_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = malicious_catalog.main()
    assert r["naive_paid"] == Decimal("450.00")
    assert r["guarded_paid"] == Decimal("12.00") and r["audit_ok"]


def test_replay_mandate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = replay_mandate.main()
    assert r["accepted"] == 1 and r["outcomes"][0][1] is True and r["audit_ok"]
    assert [o[2] for o in r["outcomes"][1:]] == [
        "replayed nonce",
        "session mismatch",
        "bad signature",
        "bad signature",
        "expired",
        "unknown user",
    ]


def test_multi_turn_drift(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = multi_turn_drift.main()
    assert r["naive_paid"] == Decimal("240.00")
    assert r["stateless_flags"] == 0 and r["first_warn_turn"] == 3
    assert r["final_needs_step_up"] and r["evil_orders"] == 0


def test_semantic_drift(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = semantic_drift.main()
    assert r["gate_only_paid"] == Decimal("60.00") and r["semantic_paid"] == Decimal(
        "0"
    )
    assert "does not match" in r["reason"]
