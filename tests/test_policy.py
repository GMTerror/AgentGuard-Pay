from decimal import Decimal
import pytest
from agentguard.mandate import Mandate
from agentguard.policy import PolicyGate, Verdict

NOW = 1_000_000
CATALOG = {
    "pen": {"merchant": "shop-a", "price": "10.00", "currency": "USD"},
    "desk": {"merchant": "shop-a", "price": "70.00", "currency": "USD"},
    "gold": {"merchant": "shop-evil", "price": "1.00", "currency": "USD"},
    "euro": {"merchant": "shop-a", "price": "1.00", "currency": "EUR"},
}


@pytest.fixture
def gate():
    m = Mandate("u1", "s1", "n1", NOW, NOW + 600, "100.00", "USD", ("shop-a",))
    return PolicyGate(m, CATALOG, clock=lambda: NOW)


def test_allow(gate):
    d = gate.check_create_order([("pen", 2)])
    assert d.verdict is Verdict.ALLOW and d.total == Decimal("20.00")


def test_over_limit(gate):
    assert gate.check_create_order([("desk", 2)]).verdict is Verdict.DENY


def test_near_limit_steps_up(gate):
    assert gate.check_create_order([("desk", 1), ("pen", 2)]).verdict is Verdict.STEP_UP


@pytest.mark.parametrize(
    "items",
    [
        [],
        [("ghost", 1)],
        [("pen", 0)],
        [("pen", -1)],
        [("pen", 1.5)],
        [("pen", True)],
        [("gold", 1)],
        [("euro", 1)],
    ],
)
def test_denied(gate, items):
    assert gate.check_create_order(items).verdict is Verdict.DENY


def test_expired_denied():
    m = Mandate("u1", "s1", "n1", NOW, NOW + 600, "100.00", "USD", ("shop-a",))
    t = [NOW]
    g = PolicyGate(m, CATALOG, clock=lambda: t[0])
    t[0] = NOW + 601
    assert g.check_create_order([("pen", 1)]).verdict is Verdict.DENY


def test_capture_happy_path(gate):
    d = gate.check_create_order([("pen", 1)])
    gate.record_order("O1", d)
    assert gate.check_capture("O1", "10.00", False).verdict is Verdict.ALLOW


def test_capture_total_changed(gate):
    gate.record_order("O1", gate.check_create_order([("pen", 1)]))
    assert gate.check_capture("O1", "99.00", False).verdict is Verdict.DENY


def test_capture_unknown_order(gate):
    assert gate.check_capture("nope", "10.00", True).verdict is Verdict.DENY


def test_capture_needs_approval(gate):
    gate.record_order("O1", gate.check_create_order([("desk", 1), ("pen", 2)]))
    assert gate.check_capture("O1", "90.00", False).verdict is Verdict.STEP_UP
    assert gate.check_capture("O1", "90.00", True).verdict is Verdict.ALLOW


def test_double_capture(gate):
    gate.record_order("O1", gate.check_create_order([("pen", 1)]))
    gate.mark_captured("O1")
    assert gate.check_capture("O1", "10.00", True).verdict is Verdict.DENY


def test_cumulative_spend(gate):
    gate.record_order("O1", gate.check_create_order([("desk", 1)]))
    gate.mark_captured("O1")
    assert gate.check_create_order([("desk", 1)]).verdict is Verdict.DENY


def test_over_limit_deny_reports_attempted_total(gate):
    d = gate.check_create_order([("desk", 2)])
    assert d.verdict is Verdict.DENY and d.total == Decimal("140.00")
