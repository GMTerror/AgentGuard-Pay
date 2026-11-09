"""Optional LLM relay for the Studio chat panel (stdlib only).

The browser never sees the provider key: the page posts to /api/llm on this
localhost server, which adds the key and forwards to an OpenAI-compatible API.

  LLM_API_KEY   (falls back to GROQ_API_KEY)  - enables the relay; absent = relay off
  LLM_BASE_URL  default https://api.groq.com/openai/v1
  LLM_KEEP_REASONING=1  keep the `reasoning` field the Studio adapter sends (default: removed, because many
                        models answer 400 "does not support reasoning.effort")

Only two paths are forwarded, bodies are size-capped, and cross-site or
DNS-rebinding requests are refused.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

ALLOWED_PATHS = {"/responses", "/chat/completions"}
MAX_BODY = 2_000_000
MAX_WAIT = 30
RETRIES = 2
DEFAULT_BASE = "https://api.groq.com/openai/v1"


def settings():
    key = os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY") or ""
    base = (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE).rstrip("/")
    return key, base


def enabled() -> bool:
    return bool(settings()[0])


def origin_ok(headers, port: int) -> bool:
    """Same-origin only: Host must be this server, and Origin (if sent) must match it."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if headers.get("Host", "") not in hosts:
        return False
    origin = headers.get("Origin")
    return origin is None or origin in {f"http://{h}" for h in hosts}


def upstream_path(request_path: str):
    """'/api/llm' -> '/responses'; '/api/llm/chat/completions' -> that; anything else -> None."""
    rest = request_path[len("/api/llm") :] or "/responses"
    return rest if rest in ALLOWED_PATHS else None


def _prepare(body: bytes) -> bytes:
    """Drop `reasoning` (unless LLM_KEEP_REASONING=1); anything unparseable is forwarded untouched."""
    if os.environ.get("LLM_KEEP_REASONING") == "1":
        return body
    try:
        obj = json.loads(body)
    except ValueError:
        return body
    if isinstance(obj, dict) and "reasoning" in obj:
        del obj["reasoning"]
        return json.dumps(obj).encode()
    return body


def retry_wait(code: int, headers, text: str):
    """Seconds to wait before retrying a 429, from Retry-After or 'try again in 15.6s'; None = do not retry."""
    if code != 429:
        return None
    wait = None
    try:
        wait = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        m = re.search(r"try again in ([0-9.]+)\s*(ms|s)", text or "")
        if m:
            wait = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
    if wait is None or wait + 0.5 > MAX_WAIT:
        return None
    return wait + 0.5


def forward(handler, port: int, opener=urllib.request.urlopen, sleep=time.sleep):
    """Handle one POST to /api/llm*. `handler` is the BaseHTTPRequestHandler."""

    def reply(code, msg):
        body = msg.encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    key, base = settings()
    if not key:
        return reply(503, "LLM relay is off: set LLM_API_KEY (or GROQ_API_KEY) in .env")
    if not origin_ok(handler.headers, port):
        return reply(403, "Forbidden (relay): Host or Origin did not match this server")
    path = upstream_path(handler.path.split("?")[0])
    if path is None:
        return reply(404, "Not found")
    try:
        n = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        n = -1
    if n <= 0 or n > MAX_BODY:
        return reply(413, "Bad or oversized body")
    body = handler.rfile.read(n)
    body = _prepare(body)
    req = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": handler.headers.get("Accept", "application/json"),
            "Authorization": f"Bearer {key}",
            "User-Agent": "agentguard-dashboard/1.0",
        },
    )
    resp = None
    for attempt in range(RETRIES + 1):
        try:
            resp = opener(req, timeout=120)
            break
        except urllib.error.HTTPError as e:
            text = e.read(4000).decode("utf-8", "replace")
            wait = retry_wait(e.code, e.headers, text)
            if wait is not None and attempt < RETRIES:
                sleep(wait)
                continue
            return reply(e.code, text)
        except Exception:
            return reply(502, "LLM provider unreachable")
    handler.send_response(resp.status)
    handler.send_header(
        "Content-Type", resp.headers.get("Content-Type", "application/json")
    )
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    while True:  # stream (SSE) straight through
        chunk = resp.read(1024)
        if not chunk:
            break
        handler.wfile.write(chunk)
        handler.wfile.flush()
    handler.close_connection = True
