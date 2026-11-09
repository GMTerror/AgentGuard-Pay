"""Deterministic gate between the agent and PayPal.

The agent only names item ids and quantities. Prices, merchants and totals
come from the trusted catalog and mandate, so untrusted product text can't
change them.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .mandate import Mandate


class Verdict(Enum):
    ALLOW = "allow"
    STEP_UP = "step_up"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    total: Decimal | None = None


def _deny(reason):
    return Decision(Verdict.DENY, reason)


class PolicyGate:
    def __init__(
        self,
        mandate: Mandate,
        catalog: dict,
        step_up_ratio: Decimal = Decimal("0.8"),
        clock=time.time,
    ):
        self._m = mandate
        self._catalog = catalog
        self._ratio = step_up_ratio
        self._clock = clock
        self._limit = Decimal(mandate.max_total)
        self._spent = Decimal("0")
        self._orders: dict = {}

    def _expired(self) -> bool:
        return self._m.expires_at <= self._clock()

    def check_create_order(self, items: list) -> Decision:
        """items: [(item_id, quantity)]. Called BEFORE creating the PayPal order."""
        if self._expired():
            return _deny("mandate expired")
        if not items:
            return _deny("empty order")
        total = Decimal("0")
        for item_id, qty in items:
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                return _deny(f"invalid quantity for {item_id}")
            entry = self._catalog.get(item_id)
            if entry is None:
                return _deny(f"unknown item {item_id}")
            if entry["merchant"] not in self._m.allowed_merchants:
                return _deny(f"merchant {entry['merchant']} not allowed")
            if entry["currency"] != self._m.currency:
                return _deny("currency mismatch")
            total += Decimal(entry["price"]) * qty
        if self._spent + total > self._limit:
            return Decision(Verdict.DENY, "exceeds mandate limit", total)
        if self._spent + total > self._limit * self._ratio:
            return Decision(Verdict.STEP_UP, "near mandate limit", total)
        return Decision(Verdict.ALLOW, "within mandate", total)

    def record_order(self, order_id: str, decision: Decision) -> None:
        """Call after PayPal returns an order id for an ALLOW/STEP_UP decision."""
        if decision.verdict is Verdict.DENY or decision.total is None:
            raise ValueError("cannot record a denied order")
        self._orders[order_id] = {
            "total": decision.total,
            "step_up": decision.verdict is Verdict.STEP_UP,
            "captured": False,
        }

    def check_capture(
        self, order_id: str, paypal_total: str, user_approved: bool
    ) -> Decision:
        """Called BEFORE capture. paypal_total is read back from PayPal's order."""
        if self._expired():
            return _deny("mandate expired")
        rec = self._orders.get(order_id)
        if rec is None:
            return _deny("unknown order")
        if rec["captured"]:
            return _deny("already captured")
        if Decimal(paypal_total) != rec["total"]:
            return _deny("order total changed since approval")
        if self._spent + rec["total"] > self._limit:
            return _deny("exceeds mandate limit")
        if rec["step_up"] and not user_approved:
            return Decision(Verdict.STEP_UP, "user approval required", rec["total"])
        return Decision(Verdict.ALLOW, "capture matches order", rec["total"])

    def mark_captured(self, order_id: str) -> None:
        rec = self._orders[order_id]
        rec["captured"] = True
        self._spent += rec["total"]
