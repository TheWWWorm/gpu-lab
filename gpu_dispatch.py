#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Durable shared admission queue. Workers keep ownership of running processes."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import socketserver
import struct
import threading
import time

from gpu_lab import TERMINAL, atomic_json
from gpu_pool import Pool
from gpu_schedule import Admission, validate_request

MARKER = "GPU_LAB_DISPATCH_ID"


class Rejected(RuntimeError):
    """Worker explicitly rejected this request: it was not accepted."""


class Uncertain(RuntimeError):
    """Connection failed after sending: acceptance must be reconciled."""


def worker_rpc(method, args=None, socket_path=None):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(30)
        # Failure before a connection is established cannot have submitted a job.
        conn.connect(socket_path)
        try:
            conn.sendall(json.dumps({"method": method, "arguments": args or {}}).encode()+b"\n")
            response = json.loads(conn.makefile("rb").readline(32*1024*1024))
        except (OSError, ValueError) as exc:
            raise Uncertain("Worker reply was lost; checking the saved submission") from exc
    if not response["ok"]:
        raise Rejected(response["error"])
    return response["result"]


class Dispatcher:
    def __init__(self, config, state, transport=worker_rpc, load=None, now=time.time):
        self.config, self.now = config, now
        self.pool = Pool(config, transport, **({"load": load} if load else {}))
        self.root = Path(state)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.records, self.aliases, self.samples = {}, {}, {}
        self.stop, self.wake = threading.Event(), threading.Event()
        self.snapshot = {"gpus": {}, "jobs": [], "active_jobs": [], "queued": []}
        self.last_refresh = None
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text())
            self.records[record["id"]] = record
            if record.get("worker_id"):
                self.aliases[record["worker_id"]] = record["id"]
        self.thread = None

    def save(self, record):
        path = self.root/(record["id"]+".json")
        atomic_json(path, record)
        # Persist both content and the rename before acknowledging a request
        # or submitting to a worker, including across an abrupt host restart.
        with path.open("rb") as saved:
            os.fsync(saved.fileno())
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def public(record):
        return {key: value for key, value in record.items() if key not in {"request", "dispatch_marker"}}

    def submit(self, args):
        args = copy.deepcopy(args)
        requested = args.pop("gpu", "auto")
        if requested not in {"auto", *self.pool.workers}:
            raise ValueError("gpu must be auto, 0 or 1")
        workload, vram = validate_request(args)
        argv, cwd, extra = args.get("argv"), args.get("cwd"), args.get("env", {})
        if not isinstance(argv, list) or not argv or len(argv) > 512 or not all(
                isinstance(a, str) and "\0" not in a and len(a) <= 65536 for a in argv):
            raise ValueError("argv must be a nonempty array of command arguments")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        timeout = args.get("timeout_seconds", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 7200:
            raise ValueError("timeout_seconds must be between 1 and 7200")
        if not isinstance(extra, dict) or MARKER in extra:
            raise ValueError("env must be an object; GPU_LAB_DISPATCH_ID is reserved")
        if not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", k)
                   and isinstance(v, str) and "\0" not in v for k, v in extra.items()):
            raise ValueError("env must contain string environment variables")
        with self.lock:
            if sum(r["status"] not in TERMINAL for r in self.records.values()) >= 64:
                raise RuntimeError("Shared queue is full (64 jobs)")
            ident = secrets.token_hex(8)
            record = {"id": ident, "status": "queued", "requested_gpu": requested,
                      "gpu_slot": requested, "display": None, "gpu_uuid": None,
                      "argv": argv, "cwd": cwd, "label": str(args.get("label", argv[0]))[:200],
                      "created_at": self.now(), "timeout_seconds": timeout,
                      "workload": workload, "vram_mb": vram,
                      "waiting_reason": "Waiting in the shared queue for an available GPU",
                      "request": args, "dispatch_marker": ident+"-"+secrets.token_hex(16)}
            self.records[ident] = record
            self.save(record)
            self.wake.set()
            return self.public(record)

    def mapped(self, job, record):
        return dict(job, id=record["id"], worker_job_id=job["id"],
                    gpu_slot=record["gpu_slot"], requested_gpu=record["requested_gpu"],
                    created_at=record["created_at"],
                    assignment_reason=record.get("assignment_reason"))

    def adopt(self, record, job):
        worker_id = job["id"]
        updated = self.mapped(job, record)
        record.update(updated, worker_id=worker_id)
        if job["status"] != "queued":
            record.pop("waiting_reason", None)
        self.aliases[worker_id] = record["id"]
        self.save(record)

    def reconcile(self, record):
        # The worker persists env before acknowledging submit. The marker makes
        # a lost reply/restart recoverable without ever submitting twice.
        slot = record["gpu_slot"]
        state = self.config["workers"][slot].get("state_path")
        if not state:
            return
        for path in Path(state).glob("jobs/*/job.json"):
            try:
                job = json.loads(path.read_text())
                if job.get("env", {}).get(MARKER) != record["dispatch_marker"]:
                    continue
                job = self.pool.contact(slot, "job", {"job_id": job["id"]})
                self.adopt(record, job)
                return
            except (OSError, ValueError, RuntimeError):
                continue

    def reason(self, record, slot, state, now):
        if not state.get("available") or not state.get("accepting", True):
            return "worker unavailable or draining"
        if state.get("queued"):
            return "earlier jobs are already assigned to this GPU"
        if not any(d.get("job_id") is None and not d.get("error") for d in state.get("displays", [])):
            return "no free virtual display"
        running = state.get("running", [])
        limits = state.get("scheduler", {})
        latest = max((j.get("started_at", 0) for j in running), default=0)
        # Auto jobs can spend their first seconds importing/compiling before
        # utilization rises. Do not confuse this startup lull with spare GPU time.
        warmup = float(self.config.get("sharing_warmup_seconds", 20))
        if running and now-latest < warmup:
            return "waiting for the previous job's GPU load to settle"
        return Admission(limits).reason(record, running, self.samples.get(slot, []), latest, now)

    def tick(self):
        fd = os.open(self.pool.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._tick()

    def _tick(self):
        snapshot = self.pool.call("status", {})
        now = self.now()
        with self.lock:
            self.snapshot, self.last_refresh = snapshot, now
            for slot, state in snapshot["gpus"].items():
                samples = self.samples.setdefault(slot, [])
                load = state.get("load")
                if state.get("available") and load and all(k in load for k in
                        ["utilization_percent", "memory_used_mib", "memory_total_mib"]):
                    samples.append((now, load))
                samples[:] = [(t, v) for t, v in samples if now-t <= 6]
            for record in self.records.values():
                if record["status"] in TERMINAL:
                    continue
                if record.get("worker_id"):
                    if not snapshot["gpus"].get(record["gpu_slot"], {}).get("available"):
                        continue
                    try:
                        job = self.pool.contact(record["gpu_slot"], "job", {"job_id": record["worker_id"]})
                        if self.mapped(job, record) != {k:record.get(k) for k in self.mapped(job, record)}:
                            self.adopt(record, job)
                        if record.get("cancel_requested") and job["status"] not in TERMINAL:
                            self.adopt(record, self.pool.contact(record["gpu_slot"], "cancel", {"job_id": record["worker_id"]}))
                    except (OSError, ValueError, RuntimeError):
                        pass
                elif record.get("dispatch_started_at"):
                    self.reconcile(record)
            # FIFO among jobs eligible for the same GPUs; a pinned wait does
            # not hold an unrelated free GPU. Heavy jobs cannot be starved.
            # A lost acknowledgement can precede the worker's visible state.
            # Reserve that GPU until reconciliation proves what happened.
            blocked = {r["gpu_slot"] for r in self.records.values()
                       if r["status"] not in TERMINAL and r.get("dispatch_started_at")
                       and not r.get("worker_id")}
            pending = sorted((r for r in self.records.values() if r["status"] == "queued" and
                              not r.get("worker_id") and not r.get("dispatch_started_at")),
                             key=lambda r:r["created_at"])
            for record in pending:
                slots = [record["requested_gpu"]] if record["requested_gpu"] != "auto" else list(self.pool.workers)
                eligible, reasons = [], []
                impossible = []
                for slot in slots:
                    state = snapshot["gpus"].get(slot, {})
                    total = state.get("load", {}).get("memory_total_mib")
                    headroom = state.get("scheduler", {}).get("memory_headroom_mb", 1024)
                    if total and record["vram_mb"]+headroom > total:
                        impossible.append(slot)
                        reasons.append(f"GPU {slot}: memory estimate exceeds capacity")
                        continue
                    reason = "waiting behind an earlier shared-queue job" if slot in blocked else self.reason(record, slot, state, now)
                    if reason:
                        reasons.append(f"GPU {slot}: {reason}")
                    else:
                        eligible.append(slot)
                if len(impossible) == len(slots):
                    record.update(status="failed", finished_at=now, error="VRAM estimate exceeds every requested GPU's capacity")
                    self.save(record)
                    continue
                if not eligible:
                    reason = "; ".join(reasons)
                    if record.get("waiting_reason") != reason:
                        record["waiting_reason"] = reason
                        self.save(record)
                    blocked.update(set(slots)-set(impossible))
                    continue
                def score(slot):
                    state = snapshot["gpus"][slot]
                    count = len(state.get("active_jobs", []))
                    load = state.get("load", {})
                    peak = max((v["utilization_percent"] for _,v in self.samples.get(slot, [])), default=100)
                    return (count > 0, peak, count, load.get("memory_used_mib", 0), slot != "1")
                slot = min(eligible, key=score)
                record.update(gpu_slot=slot, dispatch_started_at=now,
                              assignment_reason=f"GPU {slot} selected at dispatch from {len(eligible)} eligible GPU(s); idle GPUs first, then recent load",
                              waiting_reason="Dispatching to GPU "+slot)
                self.save(record)  # Durable intent before any external side effect.
                args = copy.deepcopy(record["request"])
                args["env"] = dict(args.get("env", {}), **{MARKER: record["dispatch_marker"]})
                try:
                    self.adopt(record, self.pool.contact(slot, "submit", args))
                except Rejected as exc:
                    if "draining" in str(exc) or "queue is full" in str(exc):
                        record.pop("dispatch_started_at", None)
                        record["gpu_slot"] = record["requested_gpu"]
                        record["waiting_reason"] = str(exc)
                    else:
                        record.update(status="failed", finished_at=now, error=str(exc))
                    self.save(record)
                except (ConnectionError, FileNotFoundError) as exc:
                    record.pop("dispatch_started_at", None)
                    record["gpu_slot"] = record["requested_gpu"]
                    record["waiting_reason"] = "Worker connection unavailable; waiting for recovery"
                    self.save(record)
                except Exception:
                    record["waiting_reason"] = "Checking submission outcome; will not submit a duplicate"
                    self.save(record)
                # Refresh telemetry and worker state before the next admission.
                break

    def status(self):
        with self.lock:
            result = copy.deepcopy(self.snapshot)
            def alias(ident):
                return self.aliases.get(ident, ident)
            legacy = [j for j in result.get("jobs", []) if j["id"] not in self.aliases]
            own = [self.public(r) for r in self.records.values()]
            jobs = sorted(legacy+own, key=lambda j:j["created_at"], reverse=True)
            selected = jobs[:40]+[j for j in jobs[40:] if j["status"] not in TERMINAL]
            result["jobs"] = selected
            if self.last_refresh is None or self.now()-self.last_refresh > 10:
                for state in result.get("gpus", {}).values():
                    state.update(available=False, error="Dispatcher telemetry is stale")
            for state in result.get("gpus", {}).values():
                for key in ["active_jobs", "queued"]:
                    state[key] = [alias(i) for i in state.get(key, [])]
                if state.get("active_job"):
                    state["active_job"] = alias(state["active_job"])
                for display in state.get("displays", []):
                    if display.get("job_id"):
                        display["job_id"] = alias(display["job_id"])
                state["running"] = [self.public(self.records[self.aliases[j["id"]]]) if j["id"] in self.aliases else j for j in state.get("running", [])]
            own_queued = [r["id"] for r in sorted(self.records.values(),key=lambda r:r['created_at']) if r["status"] == "queued"]
            result.update(mode="shared-queue", default_gpu="auto", scheduler_version=4,
                          active_jobs=list(dict.fromkeys([alias(i) for i in result.get("active_jobs", [])])),
                          queued=list(dict.fromkeys(own_queued+[alias(i) for i in result.get("queued", [])])),
                          shared_queued=[r["id"] for r in sorted(self.records.values(),key=lambda r:r['created_at']) if r["status"] == "queued" and not r.get("worker_id")],
                          scheduler_updated_at=self.last_refresh,
                          sharing_warmup_seconds=self.config.get("sharing_warmup_seconds", 20))
            return result

    def call(self, method, args):
        if method == "submit":
            return self.submit(args)
        if method == "status":
            return self.status()
        ident = args.get("job_id")
        with self.lock:
            record = self.records.get(self.aliases.get(ident, ident))
            if record is None:
                return self.pool.call(method, args)
            if method == "job":
                return self.public(record)
            if method == "cancel" and not record.get("worker_id"):
                if record["status"] not in TERMINAL:
                    if record.get("dispatch_started_at"):
                        record["cancel_requested"] = True
                        record["waiting_reason"] = "Cancel requested; resolving the worker's submission outcome"
                    else:
                        record.update(status="cancelled", finished_at=self.now())
                    self.save(record)
                return self.public(record)
            if not record.get("worker_id"):
                if method == "logs":
                    return {"job": self.public(record), "stdout": "", "stderr": ""}
                raise ValueError("This job has not been assigned a running display")
            result = self.pool.contact(record["gpu_slot"], method, dict(args, job_id=record["worker_id"]))
            result["gpu_slot"] = record["gpu_slot"]
            if "job" in result:
                result["job"] = self.mapped(result["job"], record)
            if "job_id" in result:
                result["job_id"] = record["id"]
            if method == "cancel":
                self.adopt(record, result)
                return self.public(record)
            return result

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                print("Dispatcher refresh failed:", str(exc), flush=True)
            self.wake.wait(1)
            self.wake.clear()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            return
        try:
            request = json.loads(self.rfile.readline(1024*1024))
            if request.get("method") not in {"status", "submit", "job", "logs", "cancel", "screenshot", "windows", "input"}:
                raise ValueError("Unsupported dispatcher method")
            result = self.server.dispatcher.call(request["method"], request.get("arguments", {}))
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            self.wfile.write(json.dumps(response).encode()+b"\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/gpu-lab/pool.json")
    parser.add_argument("--state", default="/var/lib/gpu-lab-pool/queue")
    parser.add_argument("--socket", default="/run/gpu-lab-pool/control.sock")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit("Dispatcher must run as the workstation user")
    config = json.loads(Path(args.config).read_text())
    dispatcher = Dispatcher(config, args.state)
    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.unlink(missing_ok=True)
    server = Server(str(path), Handler)
    path.chmod(0o600)
    server.dispatcher = dispatcher
    dispatcher.thread = threading.Thread(target=dispatcher.run, daemon=True)
    dispatcher.thread.start()
    def stop(*_):
        dispatcher.stop.set()
        dispatcher.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("GPU Lab shared dispatcher ready", flush=True)
    try:
        server.serve_forever()
    finally:
        dispatcher.stop.set()
        dispatcher.thread.join(timeout=35)
        server.server_close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
