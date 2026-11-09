"""Out-of-band user approval. The agent has NO tool that reaches approve()/deny();
only the user's channel (the local web page below, or your own webhook) does."""

import html
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StepUpManager:
    def __init__(self, notify=None, ttl: int = 300, clock=time.time, on_resolve=None):
        self._notify, self._ttl, self._clock, self._on_resolve = (
            notify,
            ttl,
            clock,
            on_resolve,
        )
        self._reqs: dict = {}

    def request(self, order_id: str, total, reason: str) -> str:
        rid = uuid.uuid4().hex
        self._reqs[rid] = {
            "id": rid,
            "order_id": order_id,
            "total": str(total),
            "reason": reason,
            "status": "pending",
            "expires_at": self._clock() + self._ttl,
        }
        if self._notify:
            self._notify(dict(self._reqs[rid]))
        return rid

    def status(self, rid: str) -> str:
        r = self._reqs.get(rid)
        if r is None:
            return "unknown"
        if r["status"] == "pending" and self._clock() >= r["expires_at"]:
            r["status"] = "expired"
        return r["status"]

    def get(self, rid: str) -> dict | None:
        if self.status(rid) == "unknown":
            return None
        return dict(self._reqs[rid])

    def _decide(self, rid: str, new: str) -> bool:
        if self.status(rid) != "pending":
            return False
        self._reqs[rid]["status"] = new
        if self._on_resolve:
            self._on_resolve(dict(self._reqs[rid]))
        return True

    def approve(self, rid: str) -> bool:
        return self._decide(rid, "approved")

    def deny(self, rid: str) -> bool:
        return self._decide(rid, "denied")


class _Handler(BaseHTTPRequestHandler):
    manager: StepUpManager = None

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: str):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        m = re.fullmatch(r"/r/([0-9a-f]{32})", self.path)
        r = self.manager.get(m.group(1)) if m else None
        if r is None:
            return self._send(404, "Not found")
        e = {k: html.escape(str(v)) for k, v in r.items()}
        buttons = ""
        if r["status"] == "pending":
            buttons = "".join(
                f'<form method="post" action="/{a}/{e["id"]}"><button>{a.title()}</button></form>'
                for a in ("approve", "deny")
            )
        self._send(
            200,
            f"<h2>Approve payment?</h2><p>Order {e['order_id']}<br>"
            f"Total {e['total']}<br>Why: {e['reason']}<br>Status: {e['status']}</p>{buttons}",
        )

    def do_POST(self):
        m = re.fullmatch(r"/(approve|deny)/([0-9a-f]{32})", self.path)
        if not m:
            return self._send(404, "Not found")
        ok = getattr(self.manager, m.group(1))(m.group(2))
        self._send(
            200 if ok else 409, "Done" if ok else "Could not apply (not pending)"
        )


def serve(manager: StepUpManager, port: int = 8765) -> ThreadingHTTPServer:
    """Localhost only. Run server.serve_forever() in a thread."""
    handler = type("Handler", (_Handler,), {"manager": manager})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
