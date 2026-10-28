#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""GPU Lab: unprivileged test queue, CLI and stdio MCP adapter."""
import argparse
import base64
import collections
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import uuid

from gpu_schedule import Admission, validate_request

SOCKET = "/run/gpu-lab/control.sock"
LIMIT = 8 * 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted"}


def atomic_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2)+"\n")
    tmp.replace(path)


def tail(path, limit):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size-limit))
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def xconfig(gpu, width, height):
    return f'''Section "Files"
    ModulePath "/usr/lib/x86_64-linux-gnu/nvidia/xorg"
    ModulePath "/usr/lib/xorg/modules"
EndSection
Section "ServerFlags"
    Option "AutoAddDevices" "false"
    Option "AutoEnableDevices" "false"
    Option "AutoAddGPU" "false"
    Option "AutoBindGPU" "false"
    Option "AllowMouseOpenFail" "true"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
Section "ServerLayout"
    Identifier "GPULab"
    Screen 0 "GPULabScreen"
EndSection
Section "Device"
    Identifier "GPULabGPU"
    Driver "nvidia"
    BusID "{gpu['bus_id']}"
    Option "AllowEmptyInitialConfiguration" "true"
    Option "UseDisplayDevice" "None"
    Option "ProbeAllGpus" "false"
    Option "Interactive" "false"
EndSection
Section "Screen"
    Identifier "GPULabScreen"
    Device "GPULabGPU"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Virtual {width} {height}
    EndSubSection
EndSection
'''


class Lab:
    def __init__(self, config, gpu, runtime, state, manage_display=True, require_cgroup=True, load=None):
        if os.geteuid() == 0:
            raise RuntimeError("Test daemon must never run as root")
        self.config, self.gpu = config, gpu
        self.runtime, self.state = Path(runtime), Path(state)
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state/"jobs").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.jobs, self.processes, self.groups = {}, {}, {}
        self.pending = collections.deque()
        self.active, self.threads = {}, {}
        self.stopping = False
        self.accepting = not config.get("maintenance_on_start", False)
        self.wake = threading.Event()
        self.admission = Admission(config)
        self.samples = collections.deque(maxlen=60)
        self.last_start = 0
        self.load = load
        if load is None:
            from gpu_pool import telemetry
            self.load = lambda: telemetry(self.gpu["uuid"])
        self.manage_display = manage_display
        self.display = config.get("display", ":98")
        displays = config.get("displays", [self.display])
        if not displays or len(set(displays)) != len(displays) or not all(
                re.fullmatch(r":[1-9][0-9]{1,3}", d) for d in displays):
            raise ValueError("Unique dedicated high-numbered X displays are required")
        if not 1 <= self.admission.maximum <= min(len(displays), 8):
            raise ValueError("max_concurrent must fit the configured displays (maximum 8)")
        self.cgroup = None
        if require_cgroup:
            rel = Path("/proc/self/cgroup").read_text().strip().split("0::", 1)[1]
            own = Path("/sys/fs/cgroup")/rel.lstrip("/")
            if own.name != "supervisor":
                raise RuntimeError("Missing delegated systemd supervisor subgroup")
            self.cgroup = own.parent
        self.slots = []
        for index, display in enumerate(displays[:self.admission.maximum]):
            runtime = self.runtime/("display-"+str(index))
            runtime.mkdir(mode=0o700, exist_ok=True)
            self.slots.append({"display": display, "runtime": runtime,
                               "env": self.environment(display, runtime), "xorg": None,
                               "job_id": None, "retry_at": 0, "error": None})
        self.env = self.slots[0]["env"]
        # Recover old metadata; never adopt or signal PIDs left in saved files.
        for p in (self.state/"jobs").glob("*/job.json"):
            try:
                j = json.loads(p.read_text())
                if j["status"] not in TERMINAL:
                    j.update(status="interrupted", finished_at=time.time(),
                             error="Service restarted; submit the test again")
                    atomic_json(p, j)
                self.jobs[j["id"]] = j
            except (ValueError, KeyError):
                continue
        try:
            if manage_display:
                for slot in self.slots:
                    self.start_display(slot)
        except Exception:
            for slot in self.slots:
                self.stop_display(slot)
            raise
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

    @property
    def xorg(self):
        return self.slots[0]["xorg"]

    def environment(self, display, runtime):
        home = self.config["home"]
        env = {"HOME": home, "USER": self.config["user"], "LOGNAME": self.config["user"],
               "PATH": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
               "DISPLAY": display, "XAUTHORITY": str(runtime/"Xauthority"),
               "XDG_RUNTIME_DIR": str(runtime/"xdg"), "XDG_SESSION_TYPE": "x11",
               "CUDA_VISIBLE_DEVICES": self.gpu["uuid"], "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
               "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
               "VK_DRIVER_FILES": "/usr/share/vulkan/icd.d/nvidia_icd.json",
               "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/nvidia_icd.json",
               "MESA_VK_DEVICE_SELECT": self.gpu["vendor_device"]+"!",
               "MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE": "1",
               "VK_LOADER_LAYERS_ENABLE": "VK_LAYER_MESA_device_select",
               "DXVK_FILTER_DEVICE_NAME": self.gpu["name"],
               "VKD3D_FILTER_DEVICE_NAME": self.gpu["name"], "VKD3D_VULKAN_DEVICE": "0",
               "QT_QPA_PLATFORM": "xcb", "GDK_BACKEND": "x11", "SDL_VIDEODRIVER": "x11",
               "MOZ_ENABLE_WAYLAND": "0", "ELECTRON_OZONE_PLATFORM_HINT": "x11"}
        (runtime/"xdg").mkdir(mode=0o700, exist_ok=True)
        return env

    def start_display(self, slot):
        runtime, display, env = slot["runtime"], slot["display"], slot["env"]
        auth = runtime/"Xauthority"
        auth.write_bytes(b"")
        auth.chmod(0o600)
        subprocess.run(["/usr/bin/xauth", "-f", str(auth)],
                       input=f"add {display} . {secrets.token_hex(16)}\n", text=True,
                       capture_output=True, check=True)
        conf = runtime/"xorg.conf"
        conf.write_text(xconfig(self.gpu, *self.config.get("size", [2880, 1800])))
        cmd = ["/usr/lib/xorg/Xorg", display, "-seat", "gpu-lab", "-sharevts",
               "-novtswitch", "-isolateDevice", self.gpu["bus_id"], "-config", str(conf),
               "-auth", str(auth), "-nolisten", "tcp", "-noreset", "-logfile", str(runtime/"Xorg.log")]
        xorg = slot["xorg"] = subprocess.Popen(cmd, env=env)
        for _ in range(100):
            if xorg.poll() is not None:
                raise RuntimeError("Lab X server failed: "+tail(runtime/"Xorg.log", 4000))
            try:
                r = subprocess.run(["xdpyinfo"], env=env, capture_output=True, timeout=2)
                if r.returncode == 0:
                    # Check the X server's actual GPU, not just our requested environment.
                    r = subprocess.run(["nvidia-settings", "-q", "gpus", "-t"], env=env,
                                       text=True, capture_output=True, check=True, timeout=5)
                    if self.gpu["uuid"] not in r.stdout or "1 GPU" not in r.stdout:
                        raise RuntimeError("The X server is not exclusively on the configured GPU")
                    slot["error"] = None
                    return
            except subprocess.TimeoutExpired:
                pass
            time.sleep(.1)
        raise RuntimeError("Timed out waiting for the virtual display")

    @staticmethod
    def stop_display(slot):
        xorg = slot["xorg"]
        if xorg and xorg.poll() is None:
            xorg.terminate()
            try:
                xorg.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xorg.kill()
                xorg.wait()

    def save(self, job):
        atomic_json(self.state/"jobs"/job["id"]/"job.json", job)

    def submit(self, args):
        workload, vram = validate_request(args)
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or len(argv) > 512 or not all(
                isinstance(a, str) and "\0" not in a and len(a) <= 65536 for a in argv):
            raise ValueError("argv must be a nonempty array of command arguments")
        cwd = Path(args.get("cwd", self.config["home"]))
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        timeout = args.get("timeout_seconds", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 7200:
            raise ValueError("timeout_seconds must be between 1 and 7200")
        extra = args.get("env", {})
        if not isinstance(extra, dict) or not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", k)
                and isinstance(v, str) and "\0" not in v for k, v in extra.items()):
            raise ValueError("env must contain string environment variables")
        protected = set(self.env)-{"PATH", "LANG", "HOME", "USER", "LOGNAME"}
        protected |= {"HOME", "USER", "LOGNAME", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                      "NODEVICE_SELECT", "VK_INSTANCE_LAYERS", "VK_LAYER_PATH", "VK_LOADER_LAYERS_DISABLE",
                      "__NV_PRIME_RENDER_OFFLOAD", "__NV_PRIME_RENDER_OFFLOAD_PROVIDER"}
        if protected.intersection(extra):
            raise ValueError("Cannot override lab display/GPU/session variables: "+", ".join(sorted(protected.intersection(extra))))
        with self.lock:
            if self.stopping or not self.accepting:
                raise RuntimeError("Worker is draining for maintenance; submit again after it restarts")
            if self.samples and vram+self.admission.headroom > self.samples[-1][1]["memory_total_mib"]:
                raise ValueError("VRAM estimate plus reserved headroom exceeds this GPU's total memory")
            if sum(j["status"] not in TERMINAL for j in self.jobs.values()) >= 32:
                raise RuntimeError("Lab queue is full (32 jobs)")
            ident = uuid.uuid4().hex[:16]
            directory = self.state/"jobs"/ident
            directory.mkdir(mode=0o700)
            job = {"id": ident, "status": "queued", "argv": argv, "cwd": str(cwd),
                   "env": extra, "timeout_seconds": timeout, "created_at": time.time(),
                   "label": str(args.get("label", argv[0]))[:200], "directory": str(directory),
                   "gpu_uuid": self.gpu["uuid"], "display": None,
                   "workload": workload, "vram_mb": vram}
            self.jobs[ident] = job
            self.save(job)
            self.pending.append(ident)
            self.wake.set()
            return self.public(job)

    @staticmethod
    def public(job):
        # Explicit environment overrides may include secrets; do not echo them to clients.
        return {k: v for k, v in job.items() if k != "env"}

    def pipe_log(self, stream, path, job, name):
        total = 0
        with path.open("wb") as out:
            while chunk := stream.read1(65536):
                remaining = max(0, LIMIT-total)
                out.write(chunk[:remaining])
                out.flush()
                total += len(chunk)
        stream.close()
        with self.lock:
            job[name+"_truncated"] = total > LIMIT

    def cleanup_processes(self, ident):
        group = self.groups.get(ident)
        proc = self.processes.get(ident)
        if group:
            try:
                (group/"cgroup.kill").write_text("1")
            except FileNotFoundError:
                pass
        elif proc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def execute(self, job):
        ident = job["id"]
        directory = Path(job["directory"])
        slot = self.slots[self.active[ident]]
        env = slot["env"] | job["env"]
        # Each job gets its own desktop IPC and runtime directory.
        runtime = self.runtime/ident
        runtime.mkdir(mode=0o700)
        env["XDG_RUNTIME_DIR"] = str(runtime)
        rfd, wfd = os.pipe()
        proc = None
        readers = []
        outcome = "failed"
        try:
            # The child blocks before exec until the parent assigns its cgroup.
            command = [sys.executable, str(Path(__file__).resolve()), "_child", str(rfd),
                       "/usr/bin/dbus-run-session", "--", *job["argv"]]
            proc = subprocess.Popen(command, cwd=job["cwd"], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True, pass_fds=(rfd,))
            os.close(rfd)
            rfd = -1
            with self.lock:
                self.processes[ident] = proc
                if self.cgroup:
                    group = self.cgroup/("job-"+ident)
                    group.mkdir()
                    self.groups[ident] = group
                    (group/"cgroup.procs").write_text(str(proc.pid))
                job["pid"] = proc.pid
                self.save(job)
            for stream, name in [(proc.stdout, "stdout"), (proc.stderr, "stderr")]:
                t = threading.Thread(target=self.pipe_log, args=(stream, directory/(name+".log"), job, name), daemon=True)
                t.start()
                readers.append(t)
            os.write(wfd, b"1")
            os.close(wfd)
            wfd = -1
            deadline = time.monotonic()+job["timeout_seconds"]
            while proc.poll() is None:
                with self.lock:
                    if self.manage_display and slot["xorg"].poll() is not None:
                        job["error"] = "This job's virtual display stopped"
                        self.cleanup_processes(ident)
                        break
                    if job.get("cancel_requested") or self.stopping:
                        outcome = "cancelled"
                        self.cleanup_processes(ident)
                        break
                    if time.monotonic() > deadline:
                        outcome = "timed_out"
                        self.cleanup_processes(ident)
                        break
                time.sleep(.1)
            proc.wait(timeout=10)
            with self.lock:
                job["exit_code"] = proc.returncode
                if outcome not in {"cancelled", "timed_out"}:
                    outcome = "completed" if proc.returncode == 0 else "failed"
        finally:
            for fd in (rfd, wfd):
                if fd >= 0:
                    os.close(fd)
            self.cleanup_processes(ident)
            if proc:
                proc.wait(timeout=10)
            for t in readers:
                t.join(timeout=5)
            group = self.groups.pop(ident, None)
            if group:
                for _ in range(50):
                    try:
                        group.rmdir()
                        break
                    except OSError:
                        time.sleep(.02)
            self.processes.pop(ident, None)
            shutil.rmtree(runtime, ignore_errors=True)
        with self.lock:
            job["status"] = outcome

    def run_job(self, job):
        ident = job["id"]
        try:
            self.execute(job)
        except Exception as exc:
            with self.lock:
                job.update(status="failed", error=str(exc))
        finally:
            with self.lock:
                job["finished_at"] = time.time()
                self.save(job)
                self.slots[self.active.pop(ident)]["job_id"] = None
                self.threads.pop(ident, None)
                self.wake.set()

    def work(self):
        last_sample = 0
        while not self.stopping:
            now = time.time()
            if now-last_sample >= 1:
                try:
                    sample = self.load().get(self.gpu["uuid"])
                    if sample:
                        with self.lock:
                            self.samples.append((time.time(), sample))
                except Exception:
                    pass  # Admission serializes when recent telemetry is unavailable.
                last_sample = now
            # A broken display affects its own job; repair it only after that
            # job has been cleaned up. Never restart another slot's X server.
            for slot in self.slots:
                if (self.manage_display and slot["job_id"] is None and
                        slot["xorg"].poll() is not None and time.time() >= slot["retry_at"]):
                    try:
                        self.start_display(slot)
                    except Exception as exc:
                        self.stop_display(slot)
                        slot.update(error=str(exc), retry_at=time.time()+10)
            with self.lock:
                while self.pending and self.jobs[self.pending[0]]["status"] == "cancelled":
                    self.pending.popleft()
                if self.pending and not self.stopping:
                    job = self.jobs[self.pending[0]]
                    free = next((i for i, s in enumerate(self.slots) if s["job_id"] is None and
                                 (not self.manage_display or s["xorg"].poll() is None)), None)
                    running = [self.jobs[i] for i in self.active]
                    reason = self.admission.reason(job, running, list(self.samples), self.last_start, time.time())
                    if free is None:
                        reason = "waiting for a free virtual display"
                    if reason:
                        if job.get("waiting_reason") != reason:
                            job["waiting_reason"] = reason
                            self.save(job)
                    else:
                        self.pending.popleft()
                        self.active[job["id"]] = free
                        self.slots[free]["job_id"] = job["id"]
                        self.last_start = time.time()
                        job.update(status="running", started_at=self.last_start, display=self.slots[free]["display"])
                        job.pop("waiting_reason", None)
                        self.save(job)
                        thread = threading.Thread(target=self.run_job, args=(job,), daemon=True)
                        self.threads[job["id"]] = thread
                        thread.start()
            self.wake.wait(.1)
            self.wake.clear()

    def call(self, method, args):
        if method == "submit":
            return self.submit(args)
        if method == "drain":
            with self.lock:
                self.accepting = not args.get("enabled", True)
                return {"accepting": self.accepting}
        if method == "status":
            with self.lock:
                jobs = sorted(self.jobs.values(), key=lambda j:j["created_at"], reverse=True)
                return {"gpu": self.gpu, "display": self.display, "size": self.config.get("size"),
                        "active_job": next(iter(self.active), None), "active_jobs": list(self.active),
                        "running": [self.public(self.jobs[i]) for i in self.active],
                        "queued": [j["id"] for j in reversed(jobs) if j["status"] == "queued"],
                        "jobs": [self.public(j) for j in jobs[:20]], "device_isolation": bool(self.cgroup),
                        "service_uid": os.geteuid(), "xorg_pid": self.xorg.pid if self.xorg else None,
                        "accepting": self.accepting, "scheduler": self.admission.describe(),
                        "displays": [{"display": s["display"], "job_id": s["job_id"],
                                      "xorg_pid": s["xorg"].pid if s["xorg"] else None,
                                      "error": s["error"]} for s in self.slots],
                        "recent_load": self.samples[-1][1] if self.samples else None}
        ident = args.get("job_id")
        with self.lock:
            if ident not in self.jobs:
                raise ValueError("Unknown job_id")
            job = self.jobs[ident]
            if method == "job":
                return self.public(job)
            if method == "cancel":
                if job["status"] == "queued":
                    job.update(status="cancelled", finished_at=time.time())
                elif job["status"] == "running":
                    job["cancel_requested"] = True
                self.save(job)
                self.wake.set()
                return self.public(job)
            if method == "logs":
                limit = min(65536, max(1, int(args.get("max_bytes", 16000))))
                return {"job": self.public(job), "stdout": tail(Path(job["directory"])/"stdout.log", limit),
                        "stderr": tail(Path(job["directory"])/"stderr.log", limit)}
            if method in ("screenshot", "input", "windows"):
                if ident not in self.active or job["status"] != "running":
                    raise ValueError("Display interaction requires this job to be running")
                slot = self.slots[self.active[ident]]
                if method == "screenshot":
                    from x11 import screenshot
                    path = Path(job["directory"])/f"screen-{time.time_ns()}.png"
                    result, preview = screenshot(slot["env"], path)
                    result.update(job_id=ident, display=slot["display"])
                    result["image_base64"] = base64.b64encode(preview).decode()
                    return result
                if method == "windows":
                    result = subprocess.run(["xwininfo", "-root", "-tree"], env=slot["env"], capture_output=True, text=True, timeout=5)
                    return {"windows": result.stdout[-24000:]}
                events = args.get("events", [])
                if not isinstance(events, list) or not 1 <= len(events) <= 100:
                    raise ValueError("Supply 1–100 input events")
                # Xlib authorization is process-global. Isolate each input
                # connection in a subprocess with that slot's Xauthority.
                result = subprocess.run([sys.executable, str(Path(__file__).with_name("x11.py"))],
                                        input=json.dumps(events), env=slot["env"],
                                        text=True, capture_output=True, timeout=10)
                if result.returncode:
                    raise ValueError(result.stderr[-2000:])
                return {"sent": len(events), "job_id": ident}
            raise ValueError("Unknown method")

    def shutdown(self):
        self.stopping = True
        self.wake.set()
        self.worker.join(timeout=15)
        with self.lock:
            threads = list(self.threads.values())
        for thread in threads:
            thread.join(timeout=15)
        for slot in self.slots:
            self.stop_display(slot)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.geteuid():
            return
        try:
            line = self.rfile.readline(1024*1024+1)
            if len(line) > 1024*1024:
                raise ValueError("Request too large")
            req = json.loads(line)
            result = self.server.lab.call(req["method"], req.get("arguments", {}))
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(response).encode()+b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def worker_rpc(method, arguments=None, socket_path=None):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(30)
        try:
            s.connect(socket_path or os.environ.get("GPU_LAB_SOCKET", SOCKET))
        except OSError as exc:
            raise RuntimeError("GPU Lab is unavailable. Check: systemctl status gpu-lab-dispatch.service gpu-lab.service gpu-lab-gpu0.service") from exc
        s.sendall(json.dumps({"method": method, "arguments": arguments or {}}).encode()+b"\n")
        f = s.makefile("rb")
        response = json.loads(f.readline(32*1024*1024))
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return response["result"]


def rpc(method, arguments=None, socket_path=None):
    if socket_path or os.environ.get("GPU_LAB_SOCKET"):
        return worker_rpc(method, arguments, socket_path)
    pool_config = Path("/etc/gpu-lab/pool.json")
    if pool_config.exists():
        config = json.loads(pool_config.read_text())
        if config.get("dispatcher_socket"):
            return worker_rpc(method, arguments, socket_path=config["dispatcher_socket"])
        from gpu_pool import Pool
        return Pool(config, worker_rpc).call(method, arguments or {})
    args = dict(arguments or {})
    if args.pop("gpu", "auto") not in ("auto", "1"):
        raise ValueError("GPU0 worker has not been installed")
    return worker_rpc(method, args)


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


S = {"type": "string"}
ID = {"job_id": S}
TOOLS = [
    ("gpu_status", "status", "Check both GPU workers, virtual displays, the shared queue and live GPU load. Automatic jobs choose a GPU when capacity is available.", schema({}), True),
    ("gpu_submit", "submit", "Queue a GPU test with its own virtual display. Defaults: gpu=auto, workload=auto. Automatic jobs wait in a shared queue and choose an available GPU at dispatch, preferring an idle GPU before sharing. gpu_slot is auto until assigned; keep using the returned job ID. Multiple jobs may share each GPU when recent load and free VRAM permit, after a 20-second startup settling period. Use workload=heavy for benchmarks or sustained heavy work to run alone among GPU Lab jobs; light is a small workload hint. vram_mb is an optional peak VRAM estimate, not a quota. The assigned GPU is logical device 0 inside the job.",
     schema({"argv": {"type": "array", "items": S, "minItems": 1}, "cwd": S,
             "gpu": {"type": "string", "enum": ["auto", "0", "1"], "default": "auto"},
             "workload": {"type": "string", "enum": ["auto", "light", "heavy"], "default": "auto"},
             "vram_mb": {"type": "integer", "minimum": 1, "description": "Estimated peak VRAM in MiB; defaults: auto 2048, light 512, heavy 8192."},
             "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 7200}, "label": S,
             "env": {"type": "object", "additionalProperties": S}}, ["argv", "cwd"]), False),
    ("gpu_job", "job", "Read queued/running/completed job state; poll this for completion.", schema(ID, ["job_id"]), True),
    ("gpu_logs", "logs", "Read bounded stdout/stderr tails and exit status.", schema(ID | {"max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536}}, ["job_id"]), True),
    ("gpu_cancel", "cancel", "Cancel a queued/running test and terminate its descendants.", schema(ID, ["job_id"]), False),
    ("gpu_screenshot", "screenshot", "Capture the running test's virtual screen. Saves full resolution PNG and returns a scaled image. Coordinates for input use full resolution.", schema(ID, ["job_id"]), True),
    ("gpu_windows", "windows", "List windows on the virtual display for the specified running job.", schema(ID, ["job_id"]), True),
    ("gpu_input", "input", "Send key, move or click events only to the specified running test's virtual display. Keys use X11 names, e.g. Return or Control_L+a.",
     schema(ID | {"events": {"type": "array", "minItems": 1, "maxItems": 100, "items": {
         "oneOf": [schema({"type": {"const": "key"}, "key": S}, ["type", "key"]),
                   schema({"type": {"const": "move"}, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["type", "x", "y"]),
                   schema({"type": {"const": "click"}, "button": {"type": "integer", "minimum": 1, "maximum": 9}}, ["type"])]}}}, ["job_id", "events"]), False)
]


def mcp():
    for line in sys.stdin:
        req = None
        try:
            req = json.loads(line)
            method = req.get("method")
            if "id" not in req:
                continue
            if method == "initialize":
                version = req.get("params", {}).get("protocolVersion")
                if version not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
                    version = "2025-06-18"
                result = {"protocolVersion": version, "serverInfo": {"name": "gpu-lab", "version": "4.0.0"},
                          "capabilities": {"tools": {}},
                          "instructions": "Use GPU Lab for graphical GPU tests. Default gpu=auto and workload=auto use a shared queue, choosing a GPU when capacity is available. Idle GPUs are preferred before sharing; allow 20 seconds for a new job's load to settle. Multiple isolated jobs may share each GPU when load and VRAM permit. Keep the returned job ID while gpu_slot changes from auto to the assigned GPU. Running jobs are not moved. Use workload=heavy for benchmarks or sustained heavy work; it excludes other GPU Lab jobs on that GPU. Optional vram_mb estimates peak memory. Do not mark everything heavy or pin everything to GPU1. The assigned GPU is logical device 0 inside each job. Use only your own job_id for screenshots/input. CPU-only checks run outside GPU Lab. Never start Xorg, switch VTs, reset GPUs or change the physical desktop. Dispatcher and workers start at boot."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": desc, "inputSchema": spec,
                                    "annotations": {"readOnlyHint": read, "destructiveHint": not read,
                                                    "openWorldHint": not read}}
                                   for name, _, desc, spec, read in TOOLS]}
            elif method == "tools/call":
                params = req.get("params", {})
                match = next((t for t in TOOLS if t[0] == params.get("name")), None)
                if not match:
                    raise ValueError("Unknown tool")
                try:
                    data = rpc(match[1], params.get("arguments", {}))
                    image = data.pop("image_base64", None)
                    content = [{"type": "text", "text": json.dumps(data)}]
                    if image:
                        content.append({"type": "image", "data": image, "mimeType": "image/png"})
                    result = {"content": content, "isError": False}
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": req.get("id") if isinstance(req, dict) else None,
                              "error": {"code": -32600, "message": str(exc)}}), flush=True)


def serve(args):
    config = json.loads(Path(args.config).read_text())
    gpu = json.loads(Path(args.gpu).read_text())
    lab = Lab(config, gpu, args.runtime, args.state, require_cgroup=not args.development)
    socket_path = lab.runtime/"control.sock"
    socket_path.unlink(missing_ok=True)
    server = Server(str(socket_path), Handler)
    server.lab = lab
    socket_path.chmod(0o600)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    signal.signal(signal.SIGINT, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print("GPU Lab ready on "+str(socket_path), flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        lab.shutdown()
        server.server_close()
        socket_path.unlink(missing_ok=True)
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_child":
        fd = int(sys.argv[2])
        if os.read(fd, 1) != b"1":
            return 125
        os.close(fd)
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("mcp")
    sub.add_parser("status")
    daemon = sub.add_parser("serve")
    daemon.add_argument("--config", default="/etc/gpu-lab/config.json")
    daemon.add_argument("--gpu", default="/run/gpu-lab-devices/gpu.json")
    daemon.add_argument("--runtime", default="/run/gpu-lab")
    daemon.add_argument("--state", default="/var/lib/gpu-lab")
    daemon.add_argument("--development", action="store_true")
    for name in ["run", "submit"]:
        p = sub.add_parser(name)
        p.add_argument("--cwd", default=os.getcwd())
        p.add_argument("--timeout", type=float, default=300)
        p.add_argument("--label")
        p.add_argument("--gpu", choices=["auto", "0", "1"], default="auto")
        p.add_argument("--workload", choices=["auto", "light", "heavy"], default="auto")
        p.add_argument("--vram-mb", type=int, help="estimated peak VRAM in MiB")
        p.add_argument("--env", action="append", default=[])
        p.add_argument("argv", nargs=argparse.REMAINDER)
    for name in ["job", "logs", "cancel", "screenshot", "windows", "input"]:
        p = sub.add_parser(name)
        p.add_argument("job_id")
        if name == "input":
            p.add_argument("events", help="JSON array of key/move/click events")
        if name == "screenshot":
            p.add_argument("--output")
    args = parser.parse_args()
    if args.command == "mcp":
        mcp()
        return 0
    if args.command == "serve":
        return serve(args)
    try:
        if args.command in ("run", "submit"):
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            env = dict(pair.split("=", 1) for pair in args.env)
            result = rpc("submit", {"argv": argv, "cwd": args.cwd, "timeout_seconds": args.timeout,
                                    "label": args.label or (argv[0] if argv else "test"), "env": env,
                                    "gpu": args.gpu, "workload": args.workload,
                                    **({"vram_mb": args.vram_mb} if args.vram_mb is not None else {})})
            if args.command == "run":
                ident = result["id"]
                print("GPU Lab job: "+ident, file=sys.stderr)
                try:
                    while result["status"] not in TERMINAL:
                        time.sleep(.5)
                        result = rpc("job", {"job_id": ident})
                except KeyboardInterrupt:
                    rpc("cancel", {"job_id": ident})
                    return 130
                logs = rpc("logs", {"job_id": ident, "max_bytes": 65536})
                print(logs["stdout"], end="")
                print(logs["stderr"], end="", file=sys.stderr)
                if result.get("error"):
                    print(result["error"], file=sys.stderr)
                if result["status"] == "completed":
                    return 0
                return result.get("exit_code", 1) if 0 < result.get("exit_code", 1) < 256 else 1
        else:
            params = {"job_id": args.job_id} if hasattr(args, "job_id") else {}
            if args.command == "input":
                params["events"] = json.loads(args.events)
            result = rpc(args.command, params)
            if args.command == "screenshot":
                result.pop("image_base64", None)
                if args.output:
                    shutil.copyfile(result["path"], args.output)
                    result["path"] = str(Path(args.output).resolve())
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
