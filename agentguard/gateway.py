"""The ONLY thing the agent can reach. Order per action:
risk check -> policy gate -> (step-up) -> PayPal, with every decision audited."""

import hashlib
import json
from decimal import Decimal

from .catalog import CatalogError, StaticCatalog
from .paypal_client import PayPalError
from .policy import Decision, Verdict


def _parse_items(raw):
    if not isinstance(raw, list):
        return None
    out = []
    for it in raw:
        if (
            not isinstance(it, dict)
            or not isinstance(it.get("item_id"), str)
            or "quantity" not in it
        ):
            return None
        out.append((it["item_id"], it["quantity"]))
    return out


class Gateway:
    def __init__(
        self,
        mandate,
        gate,
        client,
        audit,
        stepup,
        risk,
        catalog,
        return_url="https://example.com/return",
        cancel_url="https://example.com/cancel",
        vault_token=None,
        intent=None,
    ):
        self._m, self._gate, self._client = mandate, gate, client
        self._audit, self._stepup, self._risk = audit, stepup, risk
        self._catalog = StaticCatalog(catalog) if isinstance(catalog, dict) else catalog
        self._return_url, self._cancel_url = return_url, cancel_url
        self._orders: dict = {}
        self.last_provider_error = None
        self._vault_token = vault_token
        self._auto = vault_token is not None
        self._intent = intent
        self._auto_pending: dict = {}

    # -- helpers --------------------------------------------------------
    def _ctx(self) -> dict:
        return {"risk": round(self._risk.score, 3), "session_id": self._m.session_id}

    def _fail(self, action: str, reason: str) -> dict:
        self._audit.log(
            "decision", action=action, verdict="deny", reason=reason, **self._ctx()
        )
        return {"ok": False, "error": reason}

    def _ratio(self, total) -> float:
        return float(total / Decimal(self._m.max_total)) if total else 0.0

    def _create_request_id(self, items) -> str:
        key = json.dumps([self._m.nonce, sorted(items)], default=str)
        return hashlib.sha256(key.encode()).hexdigest()[
            :32
        ]  # same order -> same id -> PayPal dedupes

    # -- tools ----------------------------------------------------------
    def search_catalog(self, query: str) -> dict:
        try:
            hits = self._catalog.search(query)
        except CatalogError as e:
            self.last_provider_error = str(e)
            self._audit.log(
                "catalog_error",
                status=e.status,
                detail=str(e),
                session_id=self._m.session_id,
            )
            return {"ok": False, "error": "catalog unavailable"}
        self._risk.update(
            untrusted_text=" ".join(
                f"{h.get('name', '')} {h.get('description', '')}" for h in hits
            )
        )
        self._audit.log(
            "search_results",
            query=query[:100],
            session_id=self._m.session_id,
            items=[
                {
                    "item_id": h["item_id"],
                    "name": h.get("name"),
                    "merchant": h.get("merchant"),
                    "price": h.get("price"),
                    "image": (self._catalog.get(h["item_id"]) or {}).get("image"),
                }
                for h in hits
            ],
        )
        return {"ok": True, "items": hits}

    def _semantic_drift(self, items: list) -> float:
        if self._intent is None:
            return 0.0
        worst = 0.0
        for item_id, _ in items:
            e = self._catalog.get(item_id)
            if e:
                worst = max(worst, self._intent.drift(e.get("name") or item_id))
        return worst

    def create_order(self, items: list) -> dict:
        if self._risk.level() == "block":
            return self._fail("create_order", "session risk too high: read-only mode")
        drift = self._semantic_drift(items)
        if drift:
            self._risk.update(semantic=drift)
        d = self._gate.check_create_order(items)
        if d.verdict is Verdict.ALLOW and self._risk.level() == "warn":
            why = (
                "item does not match what you asked for"
                if drift >= 0.5
                else "elevated session risk"
            )
            d = Decision(Verdict.STEP_UP, why, d.total)
        self._audit.log_decision(
            "create_order",
            d,
            items=items,
            semantic_drift=round(drift, 3),
            **self._ctx(),
        )
        if d.verdict is Verdict.DENY:
            self._risk.update(attempted_ratio=self._ratio(d.total), denied=True)
            return {"ok": False, "error": d.reason}
        if self._auto:
            return self._auto_buy(items, d)
        try:
            order = self._client.create_order(
                d.total,
                self._m.currency,
                self._create_request_id(items),
                self._return_url,
                self._cancel_url,
            )
        except PayPalError as e:
            self.last_provider_error = str(e)
            self._audit.log(
                "paypal_error",
                action="create_order",
                status=e.status,
                debug_id=e.debug_id,
                detail=str(e),
            )
            return {"ok": False, "error": "payment provider error"}
        self._gate.record_order(order["id"], d)
        self._orders[order["id"]] = None
        self._risk.update(attempted_ratio=self._ratio(d.total))
        return {
            "ok": True,
            "order_id": order["id"],
            "total": str(d.total),
            "approve_url": order["approve_url"],
            "needs_step_up": d.verdict is Verdict.STEP_UP,
        }

    def _auto_buy(self, items: list, d: Decision) -> dict:
        """Saved-PayPal mode: creating the order IS the payment, so any approval must come first."""
        key = self._create_request_id(items)
        if d.verdict is Verdict.STEP_UP:
            sid = self._auto_pending.get(key)
            if sid is None:
                sid = self._stepup.request(f"auto-{key[:8]}", d.total, d.reason)
                self._auto_pending[key] = sid
                self._audit.log(
                    "step_up_requested", request_id=sid, auto=True, items=items
                )
                return {"ok": False, "pending_step_up": sid}
            st = self._stepup.status(sid)
            if st == "pending":
                return {"ok": False, "pending_step_up": sid}
            if st != "approved":
                return self._fail("create_order", f"step-up {st}")
            del self._auto_pending[key]
        try:
            res = self._client.charge_saved(
                self._vault_token, d.total, self._m.currency, key
            )
        except PayPalError as e:
            self.last_provider_error = str(e)
            self._audit.log(
                "paypal_error",
                action="charge_saved",
                status=e.status,
                debug_id=e.debug_id,
                detail=str(e),
            )
            return {"ok": False, "error": "payment provider error"}
        if res["status"] != "COMPLETED":
            return self._fail("create_order", f"charge status {res['status']}")
        if res["id"] in self._orders:
            return self._fail(
                "create_order", "duplicate purchase: identical order already paid"
            )
        paid = Decimal(str(res["total"]))
        self._gate.record_order(res["id"], Decision(Verdict.ALLOW, "auto-pay", paid))
        self._orders[res["id"]] = None
        self._gate.mark_captured(res["id"])
        self._audit.log(
            "captured",
            order_id=res["id"],
            capture_id=res["capture_id"],
            total=paid,
            auto=True,
            **self._ctx(),
        )
        if paid != d.total or res["currency"] != self._m.currency:
            self._audit.log(
                "charge_mismatch",
                order_id=res["id"],
                expected=d.total,
                got=paid,
                currency=res["currency"],
            )
            return {
                "ok": False,
                "error": "charge did not match the order, see audit log",
            }
        self._risk.update(attempted_ratio=self._ratio(d.total))
        return {
            "ok": True,
            "order_id": res["id"],
            "capture_id": res["capture_id"],
            "total": str(paid),
            "paid": True,
        }

    def capture_order(self, order_id: str) -> dict:
        if self._risk.level() == "block":
            return self._fail("capture", "session risk too high: read-only mode")
        if order_id not in self._orders:
            return self._fail("capture", "unknown order")
        try:
            info = self._client.get_order(order_id)
        except PayPalError as e:
            self.last_provider_error = str(e)
            self._audit.log(
                "paypal_error",
                action="get_order",
                status=e.status,
                debug_id=e.debug_id,
                detail=str(e),
            )
            return {"ok": False, "error": "payment provider error"}
        if info["status"] != "APPROVED":
            return self._fail(
                "capture", f"buyer has not approved at PayPal (status {info['status']})"
            )
        if info["currency"] != self._m.currency:
            self._risk.update(denied=True)
            return self._fail("capture", "currency mismatch")

        sid = self._orders[order_id]
        st = self._stepup.status(sid) if sid else None
        if st in ("denied", "expired"):
            return self._fail("capture", f"step-up {st}")
        approved = st == "approved"

        d = self._gate.check_capture(order_id, info["total"], approved)
        if d.verdict is Verdict.ALLOW and not approved and self._risk.level() == "warn":
            d = Decision(Verdict.STEP_UP, "elevated session risk", d.total)
        self._audit.log_decision("capture", d, order_id=order_id, **self._ctx())

        if d.verdict is Verdict.DENY:
            self._risk.update(denied=True)
            return {"ok": False, "error": d.reason}
        if d.verdict is Verdict.STEP_UP:
            if sid is None:
                sid = self._stepup.request(order_id, d.total, d.reason)
                self._orders[order_id] = sid
                self._audit.log("step_up_requested", order_id=order_id, request_id=sid)
            return {"ok": False, "pending_step_up": sid}

        try:
            res = self._client.capture_order(order_id, request_id="cap-" + order_id)
        except PayPalError as e:
            self.last_provider_error = str(e)
            self._audit.log(
                "paypal_error",
                action="capture",
                status=e.status,
                debug_id=e.debug_id,
                detail=str(e),
            )
            return {"ok": False, "error": "payment provider error"}
        if res["status"] != "COMPLETED":
            return self._fail("capture", f"capture status {res['status']}")
        self._gate.mark_captured(order_id)
        self._audit.log(
            "captured",
            order_id=order_id,
            capture_id=res["capture_id"],
            total=d.total,
            **self._ctx(),
        )
        return {
            "ok": True,
            "order_id": order_id,
            "capture_id": res["capture_id"],
            "total": str(d.total),
        }

    def call_tool(self, name: str, args) -> dict:
        self._audit.log(
            "tool_call", tool=name, args=args, session_id=self._m.session_id
        )
        if not isinstance(args, dict):
            return self._fail(name, "arguments must be an object")
        if name == "search_catalog" and isinstance(args.get("query"), str):
            return self.search_catalog(args["query"])
        if name == "create_order":
            items = _parse_items(args.get("items"))
            return (
                self.create_order(items)
                if items is not None
                else self._fail(name, "malformed items")
            )
        if name == "capture_order" and self._auto:
            return self._fail(name, "not available in auto-pay mode")
        if name == "capture_order" and isinstance(args.get("order_id"), str):
            return self.capture_order(args["order_id"])
        return self._fail(name, "unknown tool or bad arguments")
