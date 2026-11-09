"""Thin PayPal Orders v2 client. SANDBOX ONLY by design.

This module does no policy checks. Call it only after PolicyGate has allowed the action.
Assumes a 2-decimal currency (USD, EUR, ...).
"""

import time
from decimal import Decimal

import requests

SANDBOX_URL = "https://api-m.sandbox.paypal.com"
TIMEOUT = 15
TOKEN_REFRESH_MARGIN = 60


class PayPalError(Exception):
    def __init__(self, status: int, message: str, debug_id: str | None = None):
        super().__init__(f"PayPal {status}: {message} (debug_id={debug_id})")
        self.status = status
        self.debug_id = debug_id


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        secret: str,
        base_url: str = SANDBOX_URL,
        session=None,
        clock=time.time,
    ):
        if base_url != SANDBOX_URL:
            raise ValueError("AgentGuard Pay only talks to the PayPal sandbox")
        self._id, self._secret = client_id, secret
        self._base = base_url
        self._http = session or requests.Session()
        self._clock = clock
        self._token = None
        self._token_expiry = 0.0

    def _call(self, method, path, **kw):
        resp = self._http.request(method, self._base + path, timeout=TIMEOUT, **kw)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not 200 <= resp.status_code < 300:
            raise PayPalError(
                resp.status_code,
                body.get("message") or body.get("error_description") or resp.text[:200],
                body.get("debug_id"),
            )
        return body

    def _auth_headers(self) -> dict:
        if self._token is None or self._clock() >= self._token_expiry:
            body = self._call(
                "POST",
                "/v1/oauth2/token",
                auth=(self._id, self._secret),
                data={"grant_type": "client_credentials"},
            )
            self._token = body["access_token"]
            self._token_expiry = (
                self._clock() + body["expires_in"] - TOKEN_REFRESH_MARGIN
            )
        return {"Authorization": f"Bearer {self._token}"}

    def create_order(
        self,
        total: Decimal,
        currency: str,
        request_id: str,
        return_url: str,
        cancel_url: str,
        vault: bool = False,
    ) -> dict:
        """request_id must be stable per logical order so retries don't duplicate it.
        vault=True asks PayPal to save the buyer's PayPal for later (one-time auto-pay setup)."""
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": currency,
                        "value": str(total.quantize(Decimal("0.01"))),
                    }
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "PAY_NOW",
                    }
                }
            },
        }
        if vault:
            body["payment_source"]["paypal"]["attributes"] = {
                "vault": {
                    "store_in_vault": "ON_SUCCESS",
                    "usage_type": "MERCHANT",
                    "customer_type": "CONSUMER",
                }
            }
        resp = self._call(
            "POST",
            "/v2/checkout/orders",
            json=body,
            headers={**self._auth_headers(), "PayPal-Request-Id": request_id},
        )
        approve = next(
            (
                l["href"]
                for l in resp.get("links", [])
                if l.get("rel") in ("payer-action", "approve")
            ),
            None,
        )
        return {"id": resp["id"], "status": resp["status"], "approve_url": approve}

    def get_order(self, order_id: str) -> dict:
        """Source of truth for status and total. Feed this to PolicyGate.check_capture."""
        resp = self._call(
            "GET", f"/v2/checkout/orders/{order_id}", headers=self._auth_headers()
        )
        amount = resp["purchase_units"][0]["amount"]
        return {
            "id": resp["id"],
            "status": resp["status"],
            "total": amount["value"],
            "currency": amount["currency_code"],
        }

    def capture_order(self, order_id: str, request_id: str) -> dict:
        resp = self._call(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            json={},
            headers={
                **self._auth_headers(),
                "PayPal-Request-Id": request_id,
                "Prefer": "return=representation",
            },
        )
        try:
            capture_id = resp["purchase_units"][0]["payments"]["captures"][0]["id"]
        except (KeyError, IndexError):
            capture_id = None
        try:
            vault_id = resp["payment_source"]["paypal"]["attributes"]["vault"]["id"]
        except (KeyError, TypeError):
            vault_id = None
        return {
            "id": resp["id"],
            "status": resp["status"],
            "capture_id": capture_id,
            "vault_id": vault_id,
        }

    def charge_saved(
        self, token_id: str, total: Decimal, currency: str, request_id: str
    ) -> dict:
        """Pay with the saved PayPal, no buyer present. Call only after PolicyGate (and step-up) allowed it."""
        auth = self._auth_headers()
        resp = self._call(
            "POST",
            "/v2/checkout/orders",
            headers={
                **auth,
                "PayPal-Request-Id": request_id,
                "Prefer": "return=representation",
            },
            json={
                "intent": "CAPTURE",
                "payment_source": {
                    "token": {"id": token_id, "type": "PAYMENT_METHOD_TOKEN"}
                },
                "purchase_units": [
                    {
                        "amount": {
                            "currency_code": currency,
                            "value": str(total.quantize(Decimal("0.01"))),
                        }
                    }
                ],
            },
        )
        if resp.get("status") == "APPROVED":
            resp = self._call(
                "POST",
                f"/v2/checkout/orders/{resp['id']}/capture",
                json={},
                headers={
                    **auth,
                    "PayPal-Request-Id": "cap-" + request_id,
                    "Prefer": "return=representation",
                },
            )
        try:
            unit = resp["purchase_units"][0]
            capture = ((unit.get("payments") or {}).get("captures") or [{}])[0]
            amount = capture.get("amount") or unit["amount"]
            return {
                "id": resp["id"],
                "status": resp["status"],
                "capture_id": capture.get("id"),
                "total": amount["value"],
                "currency": amount["currency_code"],
            }
        except (KeyError, IndexError, TypeError):
            raise PayPalError(
                502, "unexpected response from PayPal", resp.get("debug_id")
            )
