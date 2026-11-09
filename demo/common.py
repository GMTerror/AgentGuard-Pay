"""Shared pieces for the attack demos. Fully offline and deterministic:
fake PayPal, and a scripted model that simulates one that has ALREADY been hijacked.
(To try a real model, pass a GroqLlm instead of Script; results will vary.)"""

import os
import time
import uuid
from decimal import Decimal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentguard.audit import AuditLog
from agentguard.gateway import Gateway
from agentguard.mandate import Mandate, MandateVerifier, sign_mandate
from agentguard.policy import PolicyGate
from agentguard.risk import RiskEngine, drift_score, turn_score
from agentguard.stepup import StepUpManager

OUT_DIR = "demo_out"


class FakePayPal:
    def __init__(self):
        self.orders, self.n = {}, 0

    def create_order(self, total, currency, request_id, return_url, cancel_url):
        self.n += 1
        oid = f"O{self.n}"
        self.orders[oid] = {
            "status": "CREATED",
            "currency": currency,
            "total": str(Decimal(total).quantize(Decimal("0.01"))),
        }
        return {
            "id": oid,
            "status": "PAYER_ACTION_REQUIRED",
            "approve_url": f"https://sandbox.example/pay/{oid}",
        }

    def get_order(self, oid):
        return {"id": oid, **self.orders[oid]}

    def capture_order(self, oid, request_id):
        self.orders[oid]["status"] = "COMPLETED"
        return {"id": oid, "status": "COMPLETED", "capture_id": "C" + oid}

    def buyer_approves(self, oid):
        self.orders[oid]["status"] = "APPROVED"

    def charge_saved(self, token, total, currency, request_id):
        o = self.create_order(total, currency, request_id, "u", "u")
        self.orders[o["id"]]["status"] = "COMPLETED"
        return {
            "id": o["id"],
            "status": "COMPLETED",
            "capture_id": "C" + o["id"],
            "total": self.orders[o["id"]]["total"],
            "currency": currency,
        }


class NaiveGateway:
    """What most agent demos do: trust the model, pay whatever it asks."""

    def __init__(self, catalog, paypal):
        self._catalog, self._pp = catalog, paypal
        self.paid: list = []

    def call_tool(self, name, args):
        if name == "search_catalog":
            q = args["query"].lower()
            hits = [
                {"item_id": i, **e}
                for i, e in self._catalog.items()
                if q in i.lower()
                or q in e["name"].lower()
                or q in e["description"].lower()
            ]
            return {"ok": True, "items": hits}
        if name == "create_order":
            total = sum(
                Decimal(self._catalog[i["item_id"]]["price"]) * i["quantity"]
                for i in args["items"]
            )
            o = self._pp.create_order(total, "USD", "naive", "u", "u")
            self._pp.buyer_approves(o["id"])
            self._pp.capture_order(o["id"], "naive")
            self.paid.append(total)
            return {"ok": True, "order_id": o["id"], "total": str(total), "paid": True}
        return {"ok": False, "error": "unsupported"}


class RecordingRisk(RiskEngine):
    """RiskEngine that also remembers what each user turn contributed on its own."""

    def __init__(self):
        super().__init__()
        self._turn = 0.0

    def update(
        self, untrusted_text="", attempted_ratio=0.0, denied=False, semantic=0.0
    ):
        self._turn += (
            turn_score(untrusted_text) + drift_score(attempted_ratio, denied) + semantic
        )
        return super().update(untrusted_text, attempted_ratio, denied, semantic)

    def pop_turn(self) -> float:
        t, self._turn = self._turn, 0.0
        return t


class Script:
    """Stands in for the LLM: replays fixed outputs (a callable output sees the messages)."""

    def __init__(self, outs):
        self._outs = list(outs)

    def __call__(self, system, messages, tools):
        out = self._outs.pop(0)
        return out(messages) if callable(out) else out


def call(name, **args):
    return {"text": "", "tool_call": {"name": name, "args": args}}


def say(text):
    return {"text": text, "tool_call": None}


def capture_last_order(messages):
    for m in reversed(messages):
        if (
            m["role"] == "tool"
            and isinstance(m["result"], dict)
            and m["result"].get("order_id")
        ):
            return call("capture_order", order_id=m["result"]["order_id"])
    return say("I have no order to capture.")


def _fmt_call(name, args):
    if name == "create_order":
        return (
            "create_order("
            + ", ".join(f"{i['item_id']} x{i['quantity']}" for i in args["items"])
            + ")"
        )
    return f"{name}({', '.join(repr(v) for v in args.values())})"


def _fmt_result(res):
    if res.get("pending_step_up"):
        return f"[STEP-UP] waiting for the user (request {res['pending_step_up'][:8]})"
    if not res.get("ok"):
        return f"[BLOCKED] {res.get('error')}"
    if res.get("paid"):
        return f"[PAID] ${res['total']} left the account (order {res['order_id']})"
    if "items" in res:
        return f"[OK] {len(res['items'])} result(s)"
    if "capture_id" in res:
        return f"[OK] captured ${res['total']} (order {res['order_id']})"
    return f"[OK] order {res['order_id']} for ${res['total']}" + (
        " - needs user step-up" if res.get("needs_step_up") else ""
    )


class Traced:
    """Wraps a gateway and prints every tool call and what happened."""

    def __init__(self, inner, risk=None):
        self._inner, self._risk = inner, risk

    def call_tool(self, name, args):
        print(f"  agent -> {_fmt_call(name, args)}")
        res = self._inner.call_tool(name, args)
        extra = (
            f"   risk={self._risk.score:.2f} ({self._risk.level()})"
            if self._risk
            else ""
        )
        print(f"           {_fmt_result(res)}{extra}")
        return res


class Env:
    pass


def guarded_env(
    catalog, tag, limit="100.00", merchants=("shop-a",), **gateway_kwargs
) -> Env:
    """Mandate goes through the real flow: user signs, gateway verifies once."""
    priv, now, sid = Ed25519PrivateKey.generate(), int(time.time()), f"{tag}-session"
    signed = sign_mandate(
        Mandate(
            "alice", sid, uuid.uuid4().hex, now, now + 600, limit, "USD", merchants
        ),
        priv,
    )
    mandate = MandateVerifier({"alice": priv.public_key()}).verify(signed, sid)
    os.makedirs(OUT_DIR, exist_ok=True)
    e = Env()
    e.audit_path = f"{OUT_DIR}/{tag}.jsonl"
    if os.path.exists(e.audit_path):
        os.remove(e.audit_path)
    e.pp, e.risk, e.stepup = FakePayPal(), RecordingRisk(), StepUpManager()
    e.gw = Gateway(
        mandate,
        PolicyGate(mandate, catalog),
        e.pp,
        AuditLog(e.audit_path),
        e.stepup,
        e.risk,
        catalog,
        **gateway_kwargs,
    )
    e.mandate = mandate
    return e


def banner(title):
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)


def user_says(msg):
    print(f"\nUSER: {msg}")


def agent_says(text):
    print(f"  agent says: {text}")
