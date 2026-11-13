"""The recruiter interface: a small local web server, standard library only.

GET  /                                      the page
GET  /api/state?step=&overlay=              the jobs with their totals, and what changed. No people.
GET  /api/cards?job=&group=&offset=&limit=  one screenful of one job's list (best | not_fit)
     &w=setting:25,pay:0  &f=act_now|text:wound care|school:Pine Valley  the recruiter's own weights and filters (optional)
GET  /api/detail?job=&person=               one person's full rule-by-rule explanation, with its evidence
GET  /api/coverage                          proof that every rule ran, and the outcomes seen
POST /api/publish                           write the shortlists to the configured (simulated) destinations

Every call also takes step= and overlay=. No response grows with the number of candidates:
the page asks for people a screenful at a time, and for an explanation only when one is opened.

It listens on 127.0.0.1 only. It has no login because it is a local demonstration.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config as configuration
from .matching import GROUPS
from .pipeline import PAGE_SIZE, Engine

WEB = Path(__file__).resolve().parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8")}
_engines: dict[str | None, Engine] = {}
# The page asks for several things at once (a list, an explanation, two people to compare). The engine builds
# worlds and shortlists the first time they are needed, so one request works on it at a time.
_LOCK = threading.RLock()


def engine_for(overlay: str | None) -> Engine:
    known = {o["name"] for o in configuration.list_overlays()}
    overlay = overlay if overlay in known else None        # anything unrecognised falls back to the default
    if overlay not in _engines:
        _engines[overlay] = Engine(overlay=overlay)
    return _engines[overlay]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep the connection open: the page makes many small requests

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _params(self):
        url = urlparse(self.path)
        self.q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        step = self.q.get("step", "0")
        overlay = self.q.get("overlay", getattr(self.server, "default_overlay", None)) or None
        return url.path, int(step) if _digits(step) else 0, overlay

    def _int(self, name: str, default: int, most: int) -> int:
        value = self.q.get(name, "")
        return max(0, min(int(value), most)) if _digits(value) else default

    def _api(self, path: str, step: int, overlay: str | None):
        """(payload, status) for one read. Unknown jobs, lists and people are a 404, never an error page."""
        engine = engine_for(overlay)
        step = max(0, min(step, len(engine.events)))
        if path == "/api/state":
            started = time.perf_counter()
            state = engine.state(step)
            state["meta"]["computed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return state, 200
        if path == "/api/coverage":
            return engine.coverage(step), 200
        job = self.q.get("job", "")
        if job not in engine.world(step).jobs:
            return {"error": "That job is not on file at this point."}, 404
        if path == "/api/cards":
            if self.q.get("group") not in GROUPS:
                return {"error": "Unknown list."}, 404
            weights = {}
            for part in filter(None, self.q.get("w", "").split(",")):
                name, _, value = part.partition(":")
                try:
                    weights[name] = float(value)
                except ValueError:
                    return {"error": f"The weight {part!r} could not be read."}, 400
                if not 0 <= weights[name] <= 1000:        # also refuses inf and nan
                    return {"error": f"The weight {part!r} must be between 0 and 1000."}, 400
            filters = [f.strip() for f in self.q.get("f", "").split("|") if f.strip()][:12]      # "|" so a typed phrase may hold commas
            return engine.cards(step, job, self.q["group"], self._int("offset", 0, 10**9), self._int("limit", PAGE_SIZE, 200),
                                weights or None, filters), 200
        if path == "/api/detail":
            found = engine.detail(step, job, self.q.get("person", ""))
            return (found, 200) if found else ({"error": "That person is not on this job's list."}, 404)
        return {"error": "not found"}, 404

    def do_GET(self) -> None:  # noqa: N802
        path, step, overlay = self._params()
        if path in STATIC:
            name, content_type = STATIC[path]
            body = (WEB / name).read_bytes()
            if path == "/":
                initial = escape(overlay or "", quote=True)
                body = body.replace(b'data-overlay=""', f'data-overlay="{initial}"'.encode("utf-8"))
            self._send(200, body, content_type)
        elif path.startswith("/api/"):
            try:
                with _LOCK:
                    payload, status = self._api(path, step, overlay)
            except Exception as exc:  # noqa: BLE001  (the page shows the reason and a retry button instead of hanging)
                traceback.print_exc(file=sys.stderr)
                with _LOCK:
                    _engines.pop(overlay, None)
                payload, status = {"error": f"{type(exc).__name__}: {exc}"}, 500
            self._json(payload, status)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path, step, overlay = self._params()
        declared = self.headers.get("Content-Length") or "0"
        length = min(int(declared), 1_000_000) if _digits(declared) else 0
        if length:
            self.rfile.read(length)                   # nothing is read from the body; drain it so the connection can be reused
        if path == "/api/publish":
            with _LOCK:
                engine = engine_for(overlay)
                step = max(0, min(step, len(engine.events)))
                written = engine.publish(step)
            self._json({"simulated": True, "written": written})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args) -> None:
        pass


def _digits(value: str) -> bool:
    """Plain digits only: str.isdigit() also accepts characters such as "²" that int() refuses."""
    return value.isascii() and value.isdigit() and len(value) <= 12


def serve(port: int = 8765, host: str = "127.0.0.1", overlay: str | None = None) -> None:
    """Serve the page. Local by default; a hosted demo passes host 0.0.0.0 and the port its platform gives it."""
    if overlay:
        try:
            cfg = configuration.load(overlay)
            if cfg.get("model_reader", {}).get("provider") == "openai":
                from .model_reader import openai_provider
                openai_provider(dict(cfg["model_reader"], _root=cfg["_root"]))  # check the key without making a request
        except ValueError as exc:
            sys.exit(str(exc))
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        sys.exit(f"Port {port} is not available ({exc.strerror or exc}). Try: python3 -m shortlist --port {port + 1}")
    server.default_overlay = overlay
    print(f"Tracefit is running at http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
