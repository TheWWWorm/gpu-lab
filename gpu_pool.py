# SPDX-License-Identifier: Apache-2.0
"""Route agent requests to one isolated worker per GPU without restarting jobs.

The lock spans both selection and submission, across all CLI/MCP processes.
Workers own durable job metadata, queues and cgroups; this adapter is stateless.
"""
from concurrent.futures import ThreadPoolExecutor
import csv
import fcntl
import os
from pathlib import Path
import subprocess

from gpu_schedule import validate_request


def telemetry(gpu_uuid=None):
    try:
        output = subprocess.check_output([
            "/usr/bin/nvidia-smi", "--query-gpu=uuid,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits", *(["-i", gpu_uuid] if gpu_uuid else [])],
            text=True, stderr=subprocess.DEVNULL, timeout=3)
        return {r[0].strip(): {"utilization_percent": int(r[1]), "memory_used_mib": int(r[2]),
                              "memory_total_mib": int(r[3])}
                for r in csv.reader(output.splitlines())}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


class Pool:
    def __init__(self, config, transport, load=telemetry):
        self.workers = config["workers"]
        self.lock_path = Path(config["lock_path"])
        self.transport, self.load = transport, load

    def contact(self, slot, method, args=None):
        return self.transport(method, args or {}, socket_path=self.workers[slot]["socket"])

    def states(self):
        def fetch(slot):
            try:
                status = self.contact(slot, "status")
                if status["gpu"]["uuid"] != self.workers[slot]["gpu_uuid"]:
                    raise RuntimeError("Worker GPU UUID does not match pool configuration")
                return slot, status, None
            except Exception as exc:
                return slot, None, str(exc)
        with ThreadPoolExecutor(max_workers=len(self.workers)) as executor:
            results = list(executor.map(fetch, self.workers))
        return ({slot: state for slot, state, error in results if state is not None},
                {slot: error for slot, state, error in results if error is not None})

    @staticmethod
    def decorate(result, slot):
        result = dict(result)
        result["gpu_slot"] = slot
        if "job" in result:
            result["job"] = dict(result["job"], gpu_slot=slot)
        return result

    def submit(self, args):
        args = dict(args)
        workload, vram = validate_request(args)
        requested = args.pop("gpu", "auto")
        if requested not in {"auto", *self.workers}:
            raise ValueError("gpu must be auto, 0 or 1")
        # Owner-only persistent directory is created by the installer. A lock
        # is automatically released if a client exits or the host restarts.
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            states, errors = self.states()
            if requested != "auto":
                if requested not in states:
                    raise RuntimeError(f"GPU{requested} worker unavailable: {errors.get(requested)}")
                slot = requested
            else:
                if not states:
                    raise RuntimeError("No GPU workers available: "+str(errors))
                loads = self.load()
                def score(slot):
                    state = states[slot]
                    load = loads.get(state["gpu"]["uuid"], {})
                    active = len(state.get("active_jobs", [state["active_job"]] if state["active_job"] else []))
                    maximum = state.get("scheduler", {}).get("max_concurrent", 1)
                    heavy = any(j.get("workload") == "heavy" for j in state.get("running", []))
                    blocked = (not state.get("accepting", True) or active >= maximum or heavy or
                               (workload == "heavy" and active > 0))
                    if load:
                        limits = state.get("scheduler", {})
                        if "memory_total_mib" in load and "memory_used_mib" in load:
                            blocked |= (load["memory_total_mib"]-load["memory_used_mib"] <
                                        vram+limits.get("memory_headroom_mb", 1024))
                        blocked |= load.get("utilization_percent", 0) >= limits.get("share_utilization_limit", 100)
                    pending = len(state["queued"]) + active / maximum
                    return (blocked, pending, load.get("utilization_percent", 0),
                            load.get("memory_used_mib", 0) / max(1, load.get("memory_total_mib", 1)),
                            slot != "1")
                slot = min(states, key=score)
            # Do not retry a failed submit on another worker: the first worker
            # might already have accepted it before a connection was lost.
            return self.decorate(self.contact(slot, "submit", args), slot)

    def call(self, method, args):
        if method == "submit":
            return self.submit(args)
        if method == "status":
            states, errors = self.states()
            loads = self.load()
            jobs = [self.decorate(j, slot) for slot, s in states.items() for j in s["jobs"]]
            gpus = {slot: {k: v for k, v in state.items() if k != "jobs"} |
                    {"available": True, "load": loads.get(state["gpu"]["uuid"], {})}
                    for slot, state in states.items()}
            gpus.update({slot: {"available": False, "error": error} for slot, error in errors.items()})
            return {"mode": "pool", "default_gpu": "auto", "gpus": gpus,
                    "active_jobs": [j for s in states.values() for j in
                                    s.get("active_jobs", [s["active_job"]] if s["active_job"] else [])],
                    "queued": [j for s in states.values() for j in s["queued"]],
                    "jobs": sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:40],
                    "device_isolation": bool(states) and all(s["device_isolation"] for s in states.values()),
                    "service_uid": os.geteuid()}
        # Historical GPU1 IDs remain usable after the upgrade. Resolve IDs
        # against workers instead of silently changing or migrating them.
        errors = {}
        for slot in self.workers:
            try:
                self.contact(slot, "job", {"job_id": args.get("job_id")})
            except (OSError, RuntimeError) as exc:
                if str(exc) != "Unknown job_id":
                    errors[slot] = str(exc)
                continue
            return self.decorate(self.contact(slot, method, args), slot)
        raise ValueError("Unknown job_id" + ("; unavailable workers: "+str(errors) if errors else ""))
