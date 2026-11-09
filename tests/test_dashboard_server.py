import json
import threading
import urllib.error
import urllib.request
import pytest
from agentguard.audit import AuditLog
from dashboard.server import make_server


@pytest.fixture
def served(tmp_path):
    servers = []

    def start(pinned=None):
        srv = make_server(str(tmp_path), 0, pinned)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield tmp_path, start
    for s in servers:
        s.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def test_empty_dir(served):
    _, start = served
    d = get(start() + "/api/logs")
    assert d["file"] is None and d["events"] == [] and d["files"] == []


def test_events_chain_and_incremental_fetch(served):
    tmp, start = served
    log = AuditLog(str(tmp / "live-a.jsonl"), clock=lambda: 5)
    for i in range(3):
        log.log("decision", action="create_order", verdict="allow", i=i)
    base = start()
    d = get(base + "/api/logs")
    assert d["file"] == "live-a.jsonl" and d["total"] == 3 and d["chain_ok"] is True
    assert [e["seq"] for e in d["events"]] == [0, 1, 2] and d["events"][0]["data"][
        "verdict"
    ] == "allow"
    log.log("captured", total="9.00")
    d2 = get(base + "/api/logs?for=live-a.jsonl&after=2")
    assert [e["seq"] for e in d2["events"]] == [3] and d2["events"][0][
        "event"
    ] == "captured"
    assert get(base + "/api/logs?for=live-a.jsonl&after=3")["events"] == []


def test_switching_files_resends_everything(served):
    tmp, start = served
    AuditLog(str(tmp / "old.jsonl")).log("e")
    import os, time

    time.sleep(0.02)
    AuditLog(str(tmp / "new.jsonl")).log("e")
    os.utime(tmp / "new.jsonl", None)
    d = get(start() + "/api/logs?for=old.jsonl&after=0")
    assert d["file"] == "new.jsonl" and len(d["events"]) == 1


def test_tampered_log_reports_broken_chain(served):
    tmp, start = served
    log = AuditLog(str(tmp / "t.jsonl"))
    log.log("a", x=1)
    log.log("b", x=2)
    rows = [json.loads(l) for l in open(tmp / "t.jsonl")]
    rows[0]["data"]["x"] = 99
    open(tmp / "t.jsonl", "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    assert get(start() + "/api/logs")["chain_ok"] is False


def test_half_written_last_line_is_ignored(served):
    tmp, start = served
    AuditLog(str(tmp / "p.jsonl")).log("a")
    with open(tmp / "p.jsonl", "a") as f:
        f.write('{"ts": 1, "event": "half')
    assert len(get(start() + "/api/logs")["events"]) == 1


def test_file_param_cannot_escape_the_folder(served):
    tmp, start = served
    AuditLog(str(tmp / "ok.jsonl")).log("a")
    d = get(start() + "/api/logs?file=../../etc/passwd")
    assert d["file"] == "ok.jsonl"


def test_static_files_served_and_traversal_blocked(served):
    _, start = served
    base = start()
    with urllib.request.urlopen(base + "/") as r:
        assert r.status == 200 and b"AgentGuard" in r.read()
    for bad in ("/../server.py", "/..%2fserver.py", "/vendor", "/nope.js"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + bad)
        assert e.value.code == 404


def test_config_reports_enterprise_only_when_the_file_exists(tmp_path):
    import os

    static = tmp_path / "static"
    os.makedirs(static)
    (static / "index.html").write_text("AgentGuard")
    logs = tmp_path / "logs"
    os.makedirs(logs)

    def cfg(license_key):
        srv = make_server(str(logs), 0, None, license_key, str(static))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            return {
                k: v
                for k, v in get(
                    f"http://127.0.0.1:{srv.server_address[1]}/api/config"
                ).items()
                if k != "ai"
            }
        finally:
            srv.shutdown()

    assert cfg("KEY") == {"enterprise": False, "license": "KEY"}
    os.makedirs(static / "vendor-enterprise")
    (static / "vendor-enterprise" / "ag-grid-enterprise.min.noStyle.js").write_text(
        "//"
    )
    assert cfg("KEY") == {"enterprise": True, "license": "KEY"}


def test_studio_route(tmp_path):
    import os

    studio = tmp_path / "dist"
    os.makedirs(studio / "assets")
    (studio / "index.html").write_text("STUDIO")
    (studio / "assets" / "a.js").write_text("//js")
    (tmp_path / "secret.txt").write_text("nope")
    logs = tmp_path / "logs"
    os.makedirs(logs)

    def serve(studio_dir):
        srv = make_server(str(logs), 0, None, "", str(tmp_path), str(studio_dir))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    srv, base = serve(studio)
    try:
        assert urllib.request.urlopen(base + "/studio/").read() == b"STUDIO"
        assert urllib.request.urlopen(base + "/studio/assets/a.js").read() == b"//js"
        for bad in (
            "/studio/../secret.txt",
            "/studio/..%2fsecret.txt",
            "/studio/assets",
        ):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
    finally:
        srv.shutdown()
    srv, base = serve(tmp_path / "missing")
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/studio/")
        assert e.value.code == 404 and b"npm run build" in e.value.read()
    finally:
        srv.shutdown()
