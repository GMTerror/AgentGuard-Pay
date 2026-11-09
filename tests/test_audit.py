import json
from decimal import Decimal
from agentguard.audit import AuditLog, verify_log
from agentguard.policy import Decision, Verdict


def lines(p):
    return [json.loads(l) for l in open(p) if l.strip()]


def test_chain_verifies(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p, clock=lambda: 1)
    log.log("session_start", user="u1")
    log.log("tool_call", tool="search")
    assert verify_log(p)


def test_edit_detected(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p, clock=lambda: 1)
    log.log("a", x=1)
    log.log("b", x=2)
    rows = lines(p)
    rows[0]["data"]["x"] = 999
    open(p, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    assert not verify_log(p)


def test_deleted_entry_detected(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p, clock=lambda: 1)
    for i in range(3):
        log.log("e", i=i)
    rows = lines(p)
    del rows[1]
    open(p, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    assert not verify_log(p)


def test_reopen_continues_chain(tmp_path):
    p = str(tmp_path / "a.jsonl")
    AuditLog(p, clock=lambda: 1).log("first")
    AuditLog(p, clock=lambda: 2).log("second")
    assert verify_log(p) and len(lines(p)) == 2


def test_decision_serialized(tmp_path):
    p = str(tmp_path / "a.jsonl")
    AuditLog(p, clock=lambda: 1).log_decision(
        "create_order",
        Decision(Verdict.DENY, "exceeds mandate limit", Decimal("140.00")),
        session_id="s1",
    )
    row = lines(p)[0]["data"]
    assert (
        row["verdict"] == "deny"
        and row["total"] == "140.00"
        and row["session_id"] == "s1"
    )
    assert verify_log(p)


def test_concurrent_writers_keep_the_chain_intact(tmp_path):
    import threading

    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p, clock=lambda: 1)
    ts = [
        threading.Thread(target=lambda: [log.log("e", i=i) for i in range(25)])
        for _ in range(8)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert verify_log(p) and len(lines(p)) == 200
