#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Local, read-only GPU Lab dashboard. No worker restarts or new dependencies."""
import argparse
import base64
from collections import deque
import copy
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit, parse_qs

from PIL import Image
from gpu_lab import rpc

ASSETS = Path(__file__).with_name("dashboard")


class Dashboard:
    def __init__(self, transport=rpc, interval=2, background=True):
        self.rpc, self.interval = transport, interval
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.state = {"updated_at": None, "error": None, "gpus": {}, "jobs": [],
                      "active_jobs": [], "queued": [], "history": {}}
        self.history = {}
        self.frames, self.frame_locks = {}, {}
        self.capture_slots = threading.BoundedSemaphore(2)
        self.thread = None
        if background:
            self.thread = threading.Thread(target=self.poll, daemon=True)
            self.thread.start()

    def refresh(self):
        result = self.rpc("status")
        jobs = {j["id"]: j for j in result.get("jobs", [])}
        # Worker status intentionally limits recent history. Include every
        # active/queued job even when it is older than that history window.
        for ident in result.get("active_jobs", []) + result.get("queued", []):
            if ident not in jobs:
                try:
                    jobs[ident] = self.rpc("job", {"job_id": ident})
                except (ValueError, RuntimeError):
                    continue
        now = time.time()
        with self.lock:
            for slot, gpu in result.get("gpus", {}).items():
                history = self.history.setdefault(slot, deque(maxlen=60))
                load = gpu.get("load", {}) if gpu.get("available") else {}
                history.append({"time": now, "utilization": load.get("utilization_percent"),
                                "memory": load.get("memory_used_mib")})
            self.state = result | {"jobs": sorted(jobs.values(), key=lambda j: j["created_at"], reverse=True),
                                   "updated_at": now, "error": None,
                                   "history": {k: list(v) for k, v in self.history.items()}}
            # Completed frames remain briefly available as a last view; never
            # retain an unbounded screenshot archive on disk or in memory.
            for key in list(self.frames):
                if now-self.frames[key][0] > 300:
                    del self.frames[key]
            active = set(result.get("active_jobs", []))
            for ident in list(self.frame_locks):
                if ident not in active and not any(k[0] == ident for k in self.frames):
                    del self.frame_locks[ident]

    def poll(self):
        while not self.stop.is_set():
            try:
                self.refresh()
            except Exception as exc:
                with self.lock:
                    self.state["error"] = str(exc)
            self.stop.wait(self.interval)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def preview(self, ident, full=False):
        key = (ident, full)
        with self.lock:
            mutex = self.frame_locks.setdefault(ident, threading.Lock())
        with mutex:
            with self.lock:
                cached = self.frames.get(key)
                if cached and time.time()-cached[0] < self.interval:
                    return cached[1], cached[2], False
            job = self.rpc("job", {"job_id": ident})
            if job["status"] != "running":
                if cached:
                    return cached[1], cached[2], True
                raise ValueError("This job has no live display. Select a running graphical job.")
            with self.capture_slots:
                screen = self.rpc("screenshot", {"job_id": ident})
                path = Path(screen["path"])
                expected = Path(job["directory"]).resolve()
                # Delete only the fresh artifact created by this preview RPC.
                # Existing user/agent screenshots are never scanned or pruned.
                owned = (path.parent.resolve() == expected and
                         re.fullmatch(r"screen-[0-9]+\.png", path.name) is not None and not path.is_symlink())
                try:
                    if not owned:
                        raise RuntimeError("Unexpected screenshot artifact path")
                    if full:
                        data, mime = path.read_bytes(), "image/png"
                    else:
                        image = Image.open(io.BytesIO(base64.b64decode(screen["image_base64"]))).convert("RGB")
                        image.thumbnail((1600, 1000))
                        output = io.BytesIO()
                        image.save(output, "JPEG", quality=85)
                        data, mime = output.getvalue(), "image/jpeg"
                finally:
                    if owned:
                        path.unlink(missing_ok=True)
            with self.lock:
                self.frames[key] = (time.time(), data, mime)
            return data, mime, False


class Handler(BaseHTTPRequestHandler):
    server_version = "GPULabDashboard"

    def log_message(self, *_):
        pass  # Request paths/cookies may contain job details. No access log.

    def respond(self, code, body, mime="application/json", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed_host(self):
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            self.respond(421, {"error": "Use the local GPU Lab address"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://"+host for host in allowed}:
            self.respond(403, {"error": "Cross-origin requests are not allowed"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.respond(403, {"error": "Open GPU Lab directly"})
            return False
        return True

    def authenticated(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            supplied = cookie["gpu_lab_session"].value if "gpu_lab_session" in cookie else ""
            if secrets.compare_digest(supplied, self.server.token):
                return True
        except Exception:
            pass
        self.respond(401, {"error": "Open GPU Lab from Applications to connect this browser."})
        return False

    def do_POST(self):
        if not self.allowed_host():
            return
        if self.path != "/api/session":
            self.respond(405, {"error": "The dashboard is read-only"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 1024 or self.headers.get("Content-Type") != "application/json":
                raise ValueError("Invalid request")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Invalid request")
            token = body.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(token, self.server.token):
                self.respond(403, {"error": "This launch link has expired. Open GPU Lab from Applications again."})
                return
            self.respond(200, {"ok": True}, headers={"Set-Cookie":
                f"gpu_lab_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
        except (ValueError, TypeError):
            self.respond(400, {"error": "Invalid session request"})

    def do_GET(self):
        if not self.allowed_host():
            return
        url = urlsplit(self.path)
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/icon.svg": ("icon.svg", "image/svg+xml")}
        if url.path in assets:
            name, mime = assets[url.path]
            self.respond(200, (self.server.assets/name).read_bytes(), mime)
            return
        if not url.path.startswith("/api/"):
            self.respond(404, {"error": "Not found"})
            return
        if not self.authenticated():
            return
        try:
            if url.path == "/api/status":
                self.respond(200, self.server.dashboard.snapshot())
                return
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{16})(?:/(logs|preview))?", url.path)
            if not match:
                self.respond(404, {"error": "Not found"})
                return
            ident, action = match.groups()
            if action == "preview":
                full = parse_qs(url.query).get("full") == ["1"]
                data, mime, stale = self.server.dashboard.preview(ident, full)
                headers = {"X-Preview-State": "last-frame" if stale else "live"}
                if full:
                    headers["Content-Disposition"] = f'attachment; filename="gpu-lab-{ident}.png"'
                self.respond(200, data, mime, headers)
            elif action == "logs":
                self.respond(200, self.server.dashboard.rpc("logs", {"job_id": ident, "max_bytes": 32000}))
            else:
                self.respond(200, self.server.dashboard.rpc("job", {"job_id": ident}))
        except (ValueError, RuntimeError) as exc:
            self.respond(409, {"error": str(exc)})
        except Exception:
            self.respond(503, {"error": "The GPU worker did not respond. Retrying shortly."})


def make_server(port, dashboard, token, assets=ASSETS):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.dashboard, server.token, server.assets = dashboard, token, Path(assets)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--runtime", default="/run/gpu-lab-dashboard")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("The dashboard must run as the workstation user")
    runtime = Path(args.runtime)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path = runtime/"token"
    path.write_text(token)
    path.chmod(0o600)
    dashboard = Dashboard()
    server = make_server(args.port, dashboard, token)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    signal.signal(signal.SIGINT, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"GPU Lab dashboard listening on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        dashboard.stop.set()
        server.server_close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
