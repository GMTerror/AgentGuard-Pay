import http.client
import os
import threading
import time

from agentguard.audit import AuditLog
from agentguard.gateway import Gateway
from agentguard.mandate import Mandate
from agentguard.policy import PolicyGate
from agentguard.risk import RiskEngine
from agentguard.stepup import StepUpManager, serve

CATALOG = {
    "mug@shop.com": {
        "name": "Ceramic <b>mug</b>",
        "description": "",
        "merchant": "shop.com",
        "price": "45.00",
        "currency": "USD",
        "image": "https://img.example/mug.png",
    },
    "card@shop.com": {
        "name": "Gift card",
        "description": "",
        "merchant": "shop.com",
        "price": "5.00",
        "currency": "USD",
        "image": "http://insecure.example/x.png",
    },
}


class _FakePayPal:
    def create_order(self, total, currency, request_id, return_url, cancel_url):
        return {
            "id": "ORD1",
            "status": "CREATED",
            "approve_url": "https://paypal.example/x",
        }

    def get_order(self, order_id):
        return {
            "id": order_id,
            "status": "APPROVED",
            "total": "90.00",
            "currency": "USD",
        }

    def capture_order(self, order_id, request_id):
        return {"id": order_id, "status": "COMPLETED", "capture_id": "C1"}


def _page(port, rid):
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", f"/r/{rid}")
    r = c.getresponse()
    return r.status, r.read().decode(), dict(r.getheaders())


def _setup(tmp_path):
    now = int(time.time())
    m = Mandate("u", "s", "n", now, now + 600, "100.00", "USD", ("shop.com",))
    su = StepUpManager()
    srv = serve(su, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    gw = Gateway(
        m,
        PolicyGate(m, CATALOG),
        _FakePayPal(),
        AuditLog(os.path.join(str(tmp_path), "a.jsonl")),
        su,
        RiskEngine(),
        CATALOG,
    )
    return gw, su, srv


def test_step_up_page_shows_what_is_being_approved(tmp_path):
    gw, _su, srv = _setup(tmp_path)
    try:
        assert gw.create_order([("mug@shop.com", 2)])["needs_step_up"]
        sid = gw.capture_order("ORD1")["pending_step_up"]
        status, body, headers = _page(srv.server_address[1], sid)
        assert status == 200
        assert "shop.com" in body and "2 × 45.00 USD" in body and "90.00 USD" in body
        assert "https://img.example/mug.png" in body
        assert (
            "Ceramic &lt;b&gt;mug&lt;/b&gt;" in body and "<b>mug" not in body
        )
        assert "img-src https:" in headers["Content-Security-Policy"]
        assert headers["X-Frame-Options"] == "DENY"
    finally:
        srv.shutdown()


def test_non_https_image_becomes_placeholder(tmp_path):
    gw, su, srv = _setup(tmp_path)
    try:
        rid = su.request(
            "ORD2",
            "5.00",
            "elevated session risk",
            gw._line_items([("card@shop.com", 1)]),
        )
        _, body, _ = _page(srv.server_address[1], rid)
        assert "http://insecure.example" not in body and 'class="thumb ph"' in body
    finally:
        srv.shutdown()


def test_request_without_details_still_works(tmp_path):
    _gw, su, srv = _setup(tmp_path)
    try:
        rid = su.request("ORD3", "1.00", "near mandate limit")
        status, body, _ = _page(srv.server_address[1], rid)
        assert (
            status == 200 and "Approve payment?" in body and 'class="items"' not in body
        )
    finally:
        srv.shutdown()
