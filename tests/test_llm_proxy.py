import http.client
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dashboard.server import make_server


class _Upstream(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        _Upstream.seen.update(auth=self.headers["Authorization"], path=self.path)
        _Upstream.seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"data: 1\n\n")


@pytest.fixture
def servers(tmp_path, monkeypatch):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    srv = make_server(str(tmp_path), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{up.server_address[1]}/v1")
    yield srv.server_address[1]
    srv.shutdown()
    up.shutdown()


def _post(port, path, host=None, origin=None, body=b"{}"):
    c = http.client.HTTPConnection("127.0.0.1", port)
    h = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{port}"}
    if origin:
        h["Origin"] = origin
    c.request("POST", path, body, h)
    r = c.getresponse()
    return r.status, r.read()


def test_forwards_with_key_added_server_side(servers):
    status, body = _post(servers, "/api/llm")
    assert status == 200 and b"data: 1" in body
    assert _Upstream.seen["auth"] == "Bearer sk-test"
    assert _Upstream.seen["path"] == "/v1/responses"


def test_off_without_key(servers, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert _post(servers, "/api/llm")[0] == 503


@pytest.mark.parametrize(
    "kw,code",
    [
        ({"path": "/api/llm/models"}, 404),
        ({"host": "evil.com"}, 403),
        ({"origin": "http://evil.com"}, 403),
        ({"body": b"x" * 2_000_001}, 413),
    ],
)
def test_refused(servers, kw, code):
    kw = {"path": "/api/llm", **kw}
    assert _post(servers, kw.pop("path"), **kw)[0] == code


def test_other_post_paths_refused(servers):
    assert _post(servers, "/api/logs")[0] == 405


def test_reasoning_field_is_dropped_unless_kept(servers, monkeypatch):
    import json

    body = json.dumps(
        {"model": "m", "reasoning": {"effort": "low"}, "input": []}
    ).encode()
    _post(servers, "/api/llm", body=body)
    assert json.loads(_Upstream.seen["body"]) == {"model": "m", "input": []}
    monkeypatch.setenv("LLM_KEEP_REASONING", "1")
    _post(servers, "/api/llm", body=body)
    assert "reasoning" in json.loads(_Upstream.seen["body"])


def test_rate_limit_is_waited_out_and_retried(servers, monkeypatch):
    from dashboard import llm_proxy

    calls = {"n": 0}
    real = _Upstream.do_POST

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b"Rate limit reached. Please try again in 100ms."
            self.send_response(429)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            real(self)

    monkeypatch.setattr(_Upstream, "do_POST", flaky)
    status, body = _post(servers, "/api/llm")
    assert status == 200 and calls["n"] == 2


def test_retry_wait_reads_provider_hint_and_caps_it():
    from dashboard.llm_proxy import retry_wait

    assert retry_wait(429, {}, "try again in 15.65s") == pytest.approx(16.15, abs=0.01)
    assert retry_wait(429, {"retry-after": "3"}, "") == 3.5
    assert retry_wait(429, {}, "try again in 120s") is None
    assert retry_wait(500, {}, "try again in 1s") is None
