from agentguard.audit import verify_log
from agentguard.catalog import StaticCatalog
from demo.common import Script, call, capture_last_order, say
from scripts.run_agent import build_session
from tests.test_gateway import CATALOG, FakePayPal


def test_full_live_wiring(tmp_path, capsys):
    pp = FakePayPal()
    llm = Script(
        [
            call("search_catalog", query="desk"),
            call(
                "create_order",
                items=[
                    {"item_id": "desk", "quantity": 1},
                    {"item_id": "pen", "quantity": 2},
                ],
            ),
            say("Approve it at PayPal."),
            capture_last_order,
            say("Needs your approval."),
            capture_last_order,
            say("Paid."),
        ]
    )
    s = build_session(
        "100.00",
        ("shop-a",),
        "USD",
        10,
        0,
        paypal=pp,
        catalog=StaticCatalog(CATALOG),
        llm=llm,
        out_dir=str(tmp_path),
    )
    try:
        s.agent.run("buy a desk and two pens")
        out = capsys.readouterr().out
        assert "BUYER APPROVAL" in out and "https://pay/O1" in out
        pp.buyer_approves("O1")
        s.agent.run("I approved at PayPal")
        out = capsys.readouterr().out
        assert (
            "YOUR APPROVAL NEEDED" in out
            and "127.0.0.1" in out
            and pp.orders["O1"]["status"] == "APPROVED"
        )
        s.stepup.approve(next(iter(s.stepup._reqs)))
        s.agent.run("approved in the app")
        assert pp.orders["O1"]["status"] == "COMPLETED"
    finally:
        s.close()
    assert verify_log(s.audit_path)


def test_hijacked_model_blocked_in_live_wiring(tmp_path):
    pp = FakePayPal()
    llm = Script(
        [
            call("create_order", items=[{"item_id": "gold", "quantity": 50}]),
            say("tried"),
        ]
    )
    s = build_session(
        "100.00",
        ("shop-a",),
        "USD",
        10,
        0,
        paypal=pp,
        catalog=StaticCatalog(CATALOG),
        llm=llm,
        out_dir=str(tmp_path),
    )
    try:
        s.agent.run("buy a pen")
        assert pp.n == 0
    finally:
        s.close()


def test_audit_status_handles_empty_and_real_logs(tmp_path):
    from agentguard.audit import AuditLog
    from scripts.run_agent import audit_status

    p = str(tmp_path / "x.jsonl")
    assert "No actions" in audit_status(p)
    AuditLog(p).log("e")
    assert "intact: True" in audit_status(p)


def test_prompt_does_not_mention_sandbox():
    from scripts.run_agent import LIVE_SYSTEM

    assert "sandbox" not in LIVE_SYSTEM.lower()


def test_provider_error_shown_to_user_not_agent(tmp_path, capsys):
    from agentguard.paypal_client import PayPalError

    pp = FakePayPal()

    def boom(*a, **k):
        raise PayPalError(422, "bad amount", "dbg9")

    pp.create_order = boom
    llm = Script(
        [
            call("create_order", items=[{"item_id": "pen", "quantity": 1}]),
            say("It failed."),
        ]
    )
    s = build_session(
        "100.00",
        ("shop-a",),
        "USD",
        10,
        0,
        paypal=pp,
        catalog=StaticCatalog(CATALOG),
        llm=llm,
        out_dir=str(tmp_path),
    )
    try:
        s.agent.run("buy a pen")
        out = capsys.readouterr().out
        assert "provider detail" in out and "422" in out and "dbg9" in out
        tool_result = next(m["result"] for m in s.agent.messages if m["role"] == "tool")
        assert tool_result == {"ok": False, "error": "payment provider error"}
    finally:
        s.close()
