from decimal import Decimal
import pytest
from agentguard.agent import AUTO_TOOLS, Agent
from agentguard.audit import AuditLog, verify_log
from agentguard.catalog import StaticCatalog
from agentguard.gateway import Gateway
from agentguard.mandate import Mandate
from agentguard.paypal_client import PayPalError
from agentguard.policy import PolicyGate
from agentguard.risk import RiskEngine
from agentguard.stepup import StepUpManager
from demo.common import Script, call, say
from scripts.run_agent import build_session
from tests.test_gateway import CATALOG, NOW

TOKEN = "TOKSECRET123"


class FakeAutoPayPal:
    """Mimics PayPal: deduplicates on the request id."""

    def __init__(self, status="COMPLETED", total_override=None, fail=None):
        self.by_request, self.charges = {}, []
        self.status, self.override, self.fail = status, total_override, fail

    def charge_saved(self, token, total, currency, request_id):
        if self.fail:
            raise self.fail
        if request_id in self.by_request:
            return self.by_request[request_id]
        oid = f"A{len(self.by_request) + 1}"
        res = {
            "id": oid,
            "status": self.status,
            "capture_id": "C" + oid,
            "currency": currency,
            "total": str(self.override or total.quantize(Decimal("0.01"))),
        }
        self.by_request[request_id] = res
        self.charges.append((token, total))
        return res


@pytest.fixture
def env(tmp_path):
    m = Mandate("u1", "s1", "n1", NOW, NOW + 600, "100.00", "USD", ("shop-a",))
    pp, risk, stepup = FakeAutoPayPal(), RiskEngine(), StepUpManager()
    path = str(tmp_path / "a.jsonl")
    gw = Gateway(
        m,
        PolicyGate(m, CATALOG, clock=lambda: NOW),
        pp,
        AuditLog(path),
        stepup,
        risk,
        CATALOG,
        vault_token=TOKEN,
    )
    return gw, pp, risk, stepup, path


def buy(gw, items):
    return gw.call_tool(
        "create_order", {"items": [{"item_id": i, "quantity": q} for i, q in items]}
    )


def test_allowed_purchase_pays_immediately_and_token_stays_private(env):
    gw, pp, _, _, path = env
    r = buy(gw, [("pen", 2)])
    assert (
        r["ok"]
        and r["paid"]
        and r["total"] == "20.00"
        and pp.charges == [(TOKEN, Decimal("20.00"))]
    )
    assert TOKEN not in open(path).read() and verify_log(path)


def test_over_limit_never_charges(env):
    gw, pp, *_ = env
    assert not buy(gw, [("desk", 2)])["ok"] and pp.charges == []


def test_disallowed_merchant_never_charges(env):
    gw, pp, *_ = env
    assert not buy(gw, [("gold", 1)])["ok"] and pp.charges == []


def test_near_limit_waits_for_approval_then_pays_once(env):
    gw, pp, _, stepup, _ = env
    first = buy(gw, [("desk", 1), ("pen", 2)])
    assert first["pending_step_up"] and pp.charges == []
    assert (
        buy(gw, [("desk", 1), ("pen", 2)])["pending_step_up"]
        == first["pending_step_up"]
    )
    stepup.approve(first["pending_step_up"])
    paid = buy(gw, [("desk", 1), ("pen", 2)])
    assert paid["ok"] and paid["total"] == "90.00" and len(pp.charges) == 1
    again = buy(gw, [("desk", 1), ("pen", 2)])
    assert not again["ok"] and len(pp.charges) == 1


def test_step_up_denied_or_expired_never_charges(env):
    gw, pp, _, stepup, _ = env
    sid = buy(gw, [("desk", 1), ("pen", 2)])["pending_step_up"]
    stepup.deny(sid)
    assert "denied" in buy(gw, [("desk", 1), ("pen", 2)])["error"] and pp.charges == []


def test_capture_tool_unavailable(env):
    gw, *_ = env
    assert "auto-pay" in gw.call_tool("capture_order", {"order_id": "A1"})["error"]


def test_cumulative_limit_across_purchases(env):
    gw, pp, *_ = env
    assert buy(gw, [("desk", 1)])["ok"]
    assert not buy(gw, [("desk", 1)])["ok"]
    assert len(pp.charges) == 1


def test_injected_search_forces_approval_even_for_small_buys(env):
    gw, pp, risk, *_ = env
    gw.call_tool("search_catalog", {"query": "pen"})
    assert risk.level() == "warn"
    assert "pending_step_up" in buy(gw, [("pen", 1)]) and pp.charges == []


def test_provider_error_is_generic_and_books_nothing(env):
    gw, pp, *_ = env
    pp.fail = PayPalError(500, "internal detail", "dbg")
    assert buy(gw, [("pen", 1)])["error"] == "payment provider error"
    assert "internal detail" in gw.last_provider_error
    pp.fail = None
    assert buy(gw, [("desk", 1)])["ok"]


def test_non_completed_charge_is_not_booked(env):
    gw, pp, *_ = env
    pp.status = "PAYER_ACTION_REQUIRED"
    assert "charge status" in buy(gw, [("pen", 1)])["error"]
    pp.status = "COMPLETED"
    assert buy(gw, [("desk", 1)])["ok"]


def test_total_mismatch_is_flagged_and_counted(env):
    gw, pp, _, _, path = env
    pp.override = "55.00"
    r = buy(gw, [("pen", 1)])
    assert not r["ok"] and "did not match" in r["error"]
    assert "charge_mismatch" in open(path).read()
    pp.override = None
    assert "exceeds" in buy(gw, [("desk", 1)])["error"]


def test_auto_session_wiring_in_runner(tmp_path):
    pp = FakeAutoPayPal()
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
            say("Needs your approval."),
            call(
                "create_order",
                items=[
                    {"item_id": "desk", "quantity": 1},
                    {"item_id": "pen", "quantity": 2},
                ],
            ),
            say("Bought it."),
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
        vault_token=TOKEN,
    )
    try:
        assert [t["name"] for t in s.agent._tools] == ["search_catalog", "create_order"]
        s.agent.run("buy a desk and two pens")
        assert pp.charges == []
        s.stepup.approve(next(iter(s.stepup._reqs)))
        s.agent.run("approved")
        assert len(pp.charges) == 1
    finally:
        s.close()
    assert TOKEN not in open(s.audit_path).read()


def test_auto_tools_have_no_capture():
    assert [t["name"] for t in AUTO_TOOLS] == ["search_catalog", "create_order"]
