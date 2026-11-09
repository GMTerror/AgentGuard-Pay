from decimal import Decimal
import pytest
from agentguard.paypal_client import PayPalClient, PayPalError, SANDBOX_URL


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.routes[(method, url.replace(SANDBOX_URL, ""))]


TOKEN = ("POST", "/v1/oauth2/token")
TOKEN_RESP = FakeResp(200, {"access_token": "tok", "expires_in": 3600})


def client(routes, clock=lambda: 0):
    s = FakeSession({TOKEN: TOKEN_RESP, **routes})
    return PayPalClient("id", "secret", session=s, clock=clock), s


def test_rejects_live_url():
    with pytest.raises(ValueError):
        PayPalClient("i", "s", base_url="https://api-m.paypal.com")


def test_create_order_request_and_parse():
    c, s = client(
        {
            ("POST", "/v2/checkout/orders"): FakeResp(
                201,
                {
                    "id": "O1",
                    "status": "PAYER_ACTION_REQUIRED",
                    "links": [
                        {"rel": "self", "href": "x"},
                        {"rel": "payer-action", "href": "https://pay/O1"},
                    ],
                },
            )
        }
    )
    out = c.create_order(Decimal("20"), "USD", "req-1", "https://r", "https://c")
    assert out == {
        "id": "O1",
        "status": "PAYER_ACTION_REQUIRED",
        "approve_url": "https://pay/O1",
    }
    _, _, kw = s.calls[-1]
    assert kw["json"]["purchase_units"][0]["amount"] == {
        "currency_code": "USD",
        "value": "20.00",
    }
    assert kw["json"]["intent"] == "CAPTURE"
    assert kw["headers"]["PayPal-Request-Id"] == "req-1"
    assert kw["headers"]["Authorization"] == "Bearer tok"


def test_approve_rel_fallback():
    c, _ = client(
        {
            ("POST", "/v2/checkout/orders"): FakeResp(
                201,
                {
                    "id": "O1",
                    "status": "CREATED",
                    "links": [{"rel": "approve", "href": "https://a"}],
                },
            )
        }
    )
    assert (
        c.create_order(Decimal("1"), "USD", "r", "u", "u")["approve_url"] == "https://a"
    )


def test_get_order():
    c, _ = client(
        {
            ("GET", "/v2/checkout/orders/O1"): FakeResp(
                200,
                {
                    "id": "O1",
                    "status": "APPROVED",
                    "purchase_units": [
                        {"amount": {"currency_code": "USD", "value": "20.00"}}
                    ],
                },
            )
        }
    )
    assert c.get_order("O1") == {
        "id": "O1",
        "status": "APPROVED",
        "total": "20.00",
        "currency": "USD",
    }


def test_capture():
    c, s = client(
        {
            ("POST", "/v2/checkout/orders/O1/capture"): FakeResp(
                201,
                {
                    "id": "O1",
                    "status": "COMPLETED",
                    "purchase_units": [{"payments": {"captures": [{"id": "C9"}]}}],
                },
            )
        }
    )
    assert c.capture_order("O1", "cap-1") == {
        "id": "O1",
        "status": "COMPLETED",
        "capture_id": "C9",
        "vault_id": None,
    }
    assert s.calls[-1][2]["headers"]["PayPal-Request-Id"] == "cap-1"


def test_error_includes_debug_id():
    c, _ = client(
        {
            ("GET", "/v2/checkout/orders/X"): FakeResp(
                404, {"message": "not found", "debug_id": "dbg1"}
            )
        }
    )
    with pytest.raises(PayPalError) as e:
        c.get_order("X")
    assert e.value.status == 404 and e.value.debug_id == "dbg1"


def test_token_cached_then_refreshed():
    t = [0]
    c, s = client(
        {
            ("GET", "/v2/checkout/orders/O1"): FakeResp(
                200,
                {
                    "id": "O1",
                    "status": "CREATED",
                    "purchase_units": [
                        {"amount": {"currency_code": "USD", "value": "1.00"}}
                    ],
                },
            )
        },
        clock=lambda: t[0],
    )
    c.get_order("O1")
    c.get_order("O1")
    assert sum(1 for m, u, _ in s.calls if u.endswith("/token")) == 1
    t[0] = 3600
    c.get_order("O1")
    assert sum(1 for m, u, _ in s.calls if u.endswith("/token")) == 2


def test_vault_option_adds_attributes():
    c, s = client(
        {
            ("POST", "/v2/checkout/orders"): FakeResp(
                201, {"id": "O1", "status": "CREATED", "links": []}
            )
        }
    )
    c.create_order(Decimal("1"), "USD", "r", "u", "u", vault=True)
    attrs = s.calls[-1][2]["json"]["payment_source"]["paypal"]["attributes"]["vault"]
    assert attrs == {
        "store_in_vault": "ON_SUCCESS",
        "usage_type": "MERCHANT",
        "customer_type": "CONSUMER",
    }
    c.create_order(Decimal("1"), "USD", "r2", "u", "u")
    assert "attributes" not in s.calls[-1][2]["json"]["payment_source"]["paypal"]


def test_capture_returns_vault_id():
    c, _ = client(
        {
            ("POST", "/v2/checkout/orders/O1/capture"): FakeResp(
                201,
                {
                    "id": "O1",
                    "status": "COMPLETED",
                    "payment_source": {
                        "paypal": {
                            "attributes": {"vault": {"id": "TOK1", "status": "VAULTED"}}
                        }
                    },
                    "purchase_units": [{"payments": {"captures": [{"id": "C9"}]}}],
                },
            )
        }
    )
    assert c.capture_order("O1", "r")["vault_id"] == "TOK1"


DONE = {
    "id": "A1",
    "status": "COMPLETED",
    "purchase_units": [
        {
            "amount": {"currency_code": "USD", "value": "40.00"},
            "payments": {
                "captures": [
                    {"id": "C1", "amount": {"currency_code": "USD", "value": "40.00"}}
                ]
            },
        }
    ],
}


def test_charge_saved_request_and_parse():
    c, s = client({("POST", "/v2/checkout/orders"): FakeResp(201, DONE)})
    out = c.charge_saved("TOK", Decimal("40"), "USD", "req-9")
    assert out == {
        "id": "A1",
        "status": "COMPLETED",
        "capture_id": "C1",
        "total": "40.00",
        "currency": "USD",
    }
    kw = s.calls[-1][2]
    assert kw["json"]["payment_source"] == {
        "token": {"id": "TOK", "type": "PAYMENT_METHOD_TOKEN"}
    }
    assert kw["json"]["purchase_units"][0]["amount"] == {
        "currency_code": "USD",
        "value": "40.00",
    }
    assert (
        kw["headers"]["PayPal-Request-Id"] == "req-9"
        and kw["headers"]["Prefer"] == "return=representation"
    )


def test_charge_saved_captures_if_only_approved():
    approved = {
        "id": "A1",
        "status": "APPROVED",
        "purchase_units": [{"amount": {"currency_code": "USD", "value": "40.00"}}],
    }
    c, s = client(
        {
            ("POST", "/v2/checkout/orders"): FakeResp(201, approved),
            ("POST", "/v2/checkout/orders/A1/capture"): FakeResp(201, DONE),
        }
    )
    assert c.charge_saved("TOK", Decimal("40"), "USD", "r")["status"] == "COMPLETED"
    assert s.calls[-1][1].endswith("/A1/capture")


def test_charge_saved_odd_response_is_an_error():
    c, _ = client(
        {
            ("POST", "/v2/checkout/orders"): FakeResp(
                201, {"id": "A1", "status": "COMPLETED"}
            )
        }
    )
    with pytest.raises(PayPalError) as e:
        c.charge_saved("TOK", Decimal("40"), "USD", "r")
    assert e.value.status == 502
