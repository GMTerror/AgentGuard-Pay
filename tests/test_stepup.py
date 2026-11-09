import threading
import urllib.error
import urllib.request
from agentguard.stepup import StepUpManager, serve


def test_approve_once():
    m = StepUpManager()
    rid = m.request("O1", "90.00", "near limit")
    assert m.status(rid) == "pending"
    assert m.approve(rid) and m.status(rid) == "approved"
    assert not m.deny(rid) and m.status(rid) == "approved"


def test_deny():
    m = StepUpManager()
    rid = m.request("O1", "90.00", "x")
    assert m.deny(rid) and m.status(rid) == "denied"
    assert not m.approve(rid)


def test_expiry():
    t = [0]
    m = StepUpManager(ttl=10, clock=lambda: t[0])
    rid = m.request("O1", "1", "x")
    t[0] = 10
    assert m.status(rid) == "expired" and not m.approve(rid)


def test_unknown():
    m = StepUpManager()
    assert m.status("nope") == "unknown" and not m.approve("nope") and m.get("nope") is None


def test_notify_called():
    seen = []
    m = StepUpManager(notify=seen.append)
    rid = m.request("O1", "5.00", "why")
    assert seen[0]["id"] == rid and seen[0]["order_id"] == "O1" and seen[0]["total"] == "5.00"


def test_http_flow():
    m = StepUpManager()
    rid = m.request("O1", "90.00", "<b>x</b>")
    srv = serve(m, 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/r/{rid}").read().decode()
        assert "O1" in page and "&lt;b&gt;" in page
        req = urllib.request.Request(f"http://127.0.0.1:{port}/approve/{rid}", data=b"", method="POST")
        assert urllib.request.urlopen(req).status == 200
        assert m.status(rid) == "approved"
        try:
            urllib.request.urlopen(req)
            assert False, "second decision should fail"
        except urllib.error.HTTPError as e:
            assert e.code == 409
    finally:
        srv.shutdown()


def test_on_resolve_called_once_per_decision():
    seen = []
    m = StepUpManager(on_resolve=seen.append)
    rid = m.request("O1", "5.00", "why")
    m.approve(rid)
    m.deny(rid)
    assert [(r["id"], r["status"]) for r in seen] == [(rid, "approved")]
