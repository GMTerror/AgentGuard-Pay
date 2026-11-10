"""Out-of-band user approval. The agent has NO tool that reaches approve()/deny();
only the user's channel (the local web page below, or your own webhook) does."""

import html
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
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

    def request(self, order_id: str, total, reason: str, details=None) -> str:
        """details (optional): line items the user is approving, as built by the gateway from its trusted
        catalog snapshot: [{name, merchant, price, currency, quantity, image}]."""
        rid = uuid.uuid4().hex
        self._reqs[rid] = {
            "id": rid,
            "order_id": order_id,
            "total": str(total),
            "reason": reason,
            "status": "pending",
            "expires_at": self._clock() + self._ttl,
        }
        if details:
            self._reqs[rid]["details"] = details
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


_CSS = """
:root{color-scheme:light dark;--bg:#f1f5f9;--card:#fff;--text:#0f172a;--muted:#64748b;--line:#e2e8f0;
--ok:#16a34a;--bad:#dc2626;--warn:#d97706;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--card:#111b2e;--text:#f1f5f9;--muted:#94a3b8;--line:#22314d}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:var(--bg);color:var(--text);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.card{width:100%;max-width:440px;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:28px;box-shadow:0 10px 30px rgba(0,0,0,.08)}
.brand{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}
h1{font-size:24px;margin:0 0 18px}
dl{margin:0 0 22px;border-top:1px solid var(--line)}
.row{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid var(--line)}
dt{color:var(--muted)}
dd{margin:0;text-align:right;font-weight:600;word-break:break-all}
dd.total{font-size:22px}
.why{font-weight:500, text-transform:capitalize}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:700;
text-transform:capitalize;color:#fff;background:var(--muted)}
.badge.pending{background:var(--warn)}.badge.approved{background:var(--ok)}
.badge.denied,.badge.expired{background:var(--bad)}
.actions{display:flex;gap:12px}
.actions form{flex:1;margin:0}
button{width:100%;padding:14px;border-radius:10px;border:2px solid transparent;font:inherit;
font-weight:700;cursor:pointer;transition:filter .15s,transform .05s}
button:hover{filter:brightness(1.08)}button:active{transform:translateY(1px)}
button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.approve button{background:var(--ok);color:#fff}
.deny button{background:transparent;color:var(--bad);border-color:var(--bad)}
.items{list-style:none;margin:0 0 18px;padding:0}
.items li{display:flex;align-items:center;gap:14px;padding:12px 0;border-top:1px solid var(--line)}
.items li:last-child{border-bottom:1px solid var(--line)}
.thumb{flex:none;width:64px;height:64px;border-radius:10px;border:1px solid var(--line);background:#fff;
object-fit:contain}
.ph{display:grid;place-items:center;background:var(--bg);color:var(--muted);font-weight:700;font-size:22px}
.meta{flex:1;min-width:0}
.name{font-weight:600;overflow-wrap:anywhere}
.sub{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.line{font-weight:700;white-space:nowrap}
.listed{margin:0 0 18px;font-size:12px;color:var(--muted)}
.note{margin:16px 0 0;font-size:13px;color:var(--muted)}
.big{font-size:20px;font-weight:700;margin:0 0 6px}
"""

_SECURITY_HEADERS = {
    # the approve button must never be framed by another page (clickjacking)
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src https:; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f'<body><main class="card"><p class="brand">AgentGuard Pay</p>{body}</main></body></html>'
    )


def _money(value, currency="") -> str:
    return html.escape(f"{value} {currency}".strip())


def _items_html(details) -> tuple:
    """(items html, merchants html). Names and images are merchant-written, so everything is escaped and
    images are https only; the merchant, price and quantity come from the gateway's own snapshot.
    """
    rows, merchants = [], []
    for it in (details or [])[:20]:
        if not isinstance(it, dict):
            continue
        name = html.escape(str(it.get("name") or "Unnamed item")[:120])
        merchant = str(it.get("merchant") or "unknown")[:80]
        cur = str(it.get("currency") or "")[:3]
        qty = it.get("quantity", 1)
        price = str(it.get("price") or "")
        if merchant not in merchants:
            merchants.append(merchant)
        try:
            line = _money((Decimal(price) * int(qty)).quantize(Decimal("0.01")), cur)
        except (InvalidOperation, ValueError, TypeError):
            line = ""
        img = it.get("image")
        if isinstance(img, str) and img.startswith("https://") and len(img) <= 2000:
            thumb = (
                f'<img class="thumb" src="{html.escape(img, quote=True)}" alt="" '
                'referrerpolicy="no-referrer" loading="lazy">'
            )
        else:
            thumb = f'<div class="thumb ph">{html.escape(name[:1].upper())}</div>'
        rows.append(
            f'<li>{thumb}<div class="meta"><div class="name">{name}</div>'
            f'<div class="sub">{html.escape(merchant)} · {html.escape(str(qty))} × {_money(price, cur)}</div></div>'
            f'<div class="line">{line}</div></li>'
        )
    return ("".join(rows), ", ".join(html.escape(m) for m in merchants))


class _Handler(BaseHTTPRequestHandler):
    manager: StepUpManager = None

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: str, styled: bool = False):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if styled:
            for k, v in _SECURITY_HEADERS.items():
                self.send_header(k, v)
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
            forms = "".join(
                f'<form method="post" action="/{a}/{e["id"]}" class="{a}"><button>{a.title()}</button></form>'
                for a in ("approve", "deny")
            )
            buttons = f'<div class="actions">{forms}</div>'
            note = "Only you can approve this. The agent has no way to."
        else:
            note = "This request has already been decided or has expired."
        rows, merchants = _items_html(r.get("details"))
        items = (
            f'<ul class="items">{rows}</ul>'
            '<p class="listed">Item names and pictures are written by the merchant. '
            "Merchant, prices and the total are checked by AgentGuard.</p>"
            if rows
            else ""
        )
        merchant_row = (
            f'<div class="row"><dt>Merchant</dt><dd>{merchants}</dd></div>'
            if merchants
            else ""
        )
        self._send(
            200,
            _page(
                "Approve payment?",
                f"<h1>Approve payment?</h1>{items}<dl>{merchant_row}"
                f'<div class="row"><dt>Order</dt><dd>{e["order_id"]}</dd></div>'
                f'<div class="row"><dt>Total</dt><dd class="total">{e["total"]}</dd></div>'
                f'<div class="row"><dt>Why</dt><dd class="why">{e["reason"]}</dd></div>'
                f'<div class="row"><dt>Status</dt><dd><span class="badge {e["status"]}">{e["status"]}</span></dd></div>'
                f'</dl>{buttons}<p class="note">{note}</p>',
            ),
            styled=True,
        )

    def do_POST(self):
        m = re.fullmatch(r"/(approve|deny)/([0-9a-f]{32})", self.path)
        if not m:
            return self._send(404, "Not found")
        ok = getattr(self.manager, m.group(1))(m.group(2))
        if ok:
            body = (
                '<p class="big">Done</p>'
                f'<p class="note">You {"approved" if m.group(1) == "approve" else "denied"} this payment. '
                "You can close this tab and go back to the agent.</p>"
            )
        else:
            body = (
                '<p class="big">Could not apply (not pending)</p>'
                '<p class="note">This request was already decided or has expired.</p>'
            )
        self._send(200 if ok else 409, _page("AgentGuard Pay", body), styled=True)


def serve(manager: StepUpManager, port: int = 8765) -> ThreadingHTTPServer:
    """Localhost only. Run server.serve_forever() in a thread."""
    handler = type("Handler", (_Handler,), {"manager": manager})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
