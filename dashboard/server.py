"""Read-only dashboard server (stdlib only, localhost only).

  python -m dashboard.server                 # newest log in demo_out/
  python -m dashboard.server --dir demo_out --port 8800

GET /                 the control panel
GET /studio/           the AG Studio version (after: cd studio && npm install && npm run build)
GET /api/config       edition, license key and whether the AI relay is on (localhost only)
POST /api/llm         relay for the Studio chat panel to an OpenAI-compatible API (off unless LLM_API_KEY is set)
GET /api/logs         audit events as JSON. Params:
                        file   "auto" (newest) or a log file name
                        for    the file the caller already holds events from
                        after  last event number the caller has (only used when `for` matches)
"""

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from agentguard.audit import verify_log
from dashboard import llm_proxy

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ENTERPRISE_FILE = os.path.join("vendor-enterprise", "ag-grid-enterprise.min.noStyle.js")
STUDIO_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "studio", "dist"
)


def list_logs(log_dir: str) -> list:
    try:
        names = [n for n in os.listdir(log_dir) if n.endswith(".jsonl")]
    except FileNotFoundError:
        return []
    names.sort(key=lambda n: os.path.getmtime(os.path.join(log_dir, n)), reverse=True)
    return names[:50]


def read_events(path: str, after: int) -> tuple:
    """(total complete events, events with seq > after). A half-written last line is ignored."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    if not text.endswith("\n"):
        lines = lines[:-1]
    events, seq = [], -1
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        seq += 1
        if seq > after:
            events.append(
                {
                    "seq": seq,
                    "ts": e.get("ts"),
                    "event": e.get("event"),
                    "data": e.get("data", {}),
                    "hash": str(e.get("hash", ""))[:8],
                }
            )
    return seq + 1, events


def logs_response(
    log_dir: str, pinned, file_param: str, known: str, after: int
) -> dict:
    files = list_logs(log_dir)
    name = pinned or (
        file_param if file_param in files else (files[0] if files else None)
    )
    empty = {"file": None, "files": files, "total": 0, "chain_ok": None, "events": []}
    if name is None or not os.path.exists(os.path.join(log_dir, name)):
        return empty
    path = os.path.join(log_dir, name)
    total, events = read_events(path, after if known == name else -1)
    try:
        chain_ok = verify_log(path)
    except (ValueError, KeyError):
        chain_ok = None
    return {
        "file": name,
        "files": files,
        "total": total,
        "chain_ok": chain_ok,
        "events": events,
    }


def _do_post(self):
    if urlparse(self.path).path.startswith("/api/llm"):
        return llm_proxy.forward(self, self.server.server_address[1])
    self._send(405, b"Method not allowed", "text/plain")


def make_server(
    log_dir: str,
    port: int = 8800,
    pinned: str = None,
    license_key: str = "",
    static_dir: str = STATIC,
    studio_dir: str = STUDIO_DIST,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype: str, cache="no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, root: str, rel: str):
            full = os.path.realpath(os.path.join(root, rel))
            if not full.startswith(
                os.path.realpath(root) + os.sep
            ) or not os.path.isfile(full):
                return self._send(404, b"Not found", "text/plain")
            with open(full, "rb") as f:
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                self._send(
                    200,
                    f.read(),
                    ctype + ("; charset=utf-8" if ctype.startswith("text") else ""),
                    "no-cache",
                )

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/api/logs":
                q = parse_qs(url.query)
                try:
                    after = int(q.get("after", ["-1"])[0])
                except ValueError:
                    after = -1
                data = logs_response(
                    log_dir,
                    pinned,
                    q.get("file", ["auto"])[0],
                    q.get("for", [""])[0],
                    after,
                )
                return self._send(200, json.dumps(data).encode(), "application/json")
            if url.path == "/api/config":
                enterprise = os.path.isfile(os.path.join(static_dir, ENTERPRISE_FILE))
                return self._send(
                    200,
                    json.dumps(
                        {
                            "enterprise": enterprise,
                            "license": license_key,
                            "ai": llm_proxy.enabled(),
                        }
                    ).encode(),
                    "application/json",
                )
            if (
                url.path in ("/", "")
                and os.path.isdir(studio_dir)
                and "classic" not in parse_qs(url.query, keep_blank_values=True)
            ):
                self.send_response(302)
                self.send_header("Location", "/studio/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if url.path == "/studio":
                self.send_response(301)
                self.send_header("Location", "/studio/")
                self.end_headers()
                return
            if url.path.startswith("/studio/"):
                if not os.path.isdir(studio_dir):
                    return self._send(
                        404,
                        b"Studio is not built. Run: cd studio && npm install && npm run build",
                        "text/plain",
                    )
                return self._file(
                    studio_dir, url.path[len("/studio/") :] or "index.html"
                )
            return self._file(
                static_dir,
                "index.html" if url.path in ("/", "") else url.path.lstrip("/"),
            )

    Handler.do_POST = _do_post
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dir", default="demo_out", help="folder with audit .jsonl files")
    ap.add_argument(
        "--log", help="pin one log file name instead of following the newest"
    )
    ap.add_argument("--port", type=int, default=8800)
    args = ap.parse_args()
    load_dotenv()
    if args.log and os.path.basename(args.log) != args.log:
        sys.exit("--log must be a file name inside --dir")
    server = make_server(
        args.dir, args.port, args.log, os.environ.get("AG_GRID_LICENSE_KEY", "")
    )
    base = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Dashboard: {base}  (reading {args.dir}/, Ctrl+C to stop)")
    """if os.path.isdir(STUDIO_DIST):
        print(f"AG Studio: {base}/studio/")"""
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
