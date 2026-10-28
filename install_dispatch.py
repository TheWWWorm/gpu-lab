#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Install the shared dispatcher without stopping either GPU worker."""
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile
import time


def atomic_write(path, content, mode=0o644):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Refusing symlink: "+str(path))
    fd, name = tempfile.mkstemp(prefix=".dispatch-install-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
            os.fchown(out.fileno(), 0, 0)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this one-time installer with sudo")
    source, dest = Path(__file__).resolve().parent, Path("/opt/gpu-lab")
    config_path = Path("/etc/gpu-lab/pool.json")
    config = json.loads(config_path.read_text())
    worker_configs = {slot: json.loads(Path("/etc/gpu-lab/"+name).read_text())
                      for slot, name in [("0", "gpu0.json"), ("1", "config.json")]}
    account = pwd.getpwnam(worker_configs["1"]["user"])
    if account.pw_uid == 0 or worker_configs["0"]["user"] != account.pw_name or dest.is_symlink():
        raise SystemExit("Both workers must use the same ordinary workstation account")
    units = ["gpu-lab.service", "gpu-lab-gpu0.service"]
    before = {unit: command("systemctl", "show", "--value", "-p", "MainPID", unit) for unit in units}
    if "0" in before.values():
        raise SystemExit("Both workers must be running for this live upgrade")
    backup = dest/"backups"/("shared-dispatch-"+time.strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True, mode=0o700)
    originals = {}

    def install(path, content, mode=0o644):
        path = Path(path)
        if path not in originals:
            originals[path] = (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
            if path.exists():
                target = backup/str(path).lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        atomic_write(path, content, mode)

    service = "gpu-lab-dispatch.service"
    was_active = subprocess.run(["systemctl", "is-active", "--quiet", service]).returncode == 0
    try:
        for name in ["gpu_dispatch.py", "gpu_lab.py", "gpu_pool.py", "install_dispatch.py", "README.md"]:
            install(dest/name, (source/name).read_text(), 0o755 if name.endswith(".py") else 0o644)
        install(dest/"dashboard/app.js", (source/"dashboard/app.js").read_text())
        config["workers"]["0"]["state_path"] = "/var/lib/gpu-lab-gpu0"
        config["workers"]["1"]["state_path"] = "/var/lib/gpu-lab"
        config["sharing_warmup_seconds"] = 20
        install(config_path, json.dumps(config, indent=2)+"\n")
        install("/etc/systemd/system/"+service, f'''[Unit]
Description=GPU Lab shared queue and GPU dispatcher
After=gpu-lab.service gpu-lab-gpu0.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User={account.pw_name}
Group={account.pw_gid}
WorkingDirectory=/opt/gpu-lab
ExecStart=/usr/bin/python3 /opt/gpu-lab/gpu_dispatch.py
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
RuntimeDirectory=gpu-lab-pool
RuntimeDirectoryMode=0700
StateDirectory=gpu-lab-pool
StateDirectoryMode=0700
UMask=0077
Restart=on-failure
RestartSec=3
TimeoutStopSec=45
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/gpu-lab-pool /var/lib/gpu-lab-pool
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX
TasksMax=128
Nice=5
MemoryMax=512M

[Install]
WantedBy=multi-user.target
''')
        command("systemctl", "daemon-reload")
        command("systemctl", "restart" if was_active else "start", service)
        probe = '''import json,socket
with socket.socket(socket.AF_UNIX) as s:
 s.settimeout(5); s.connect('/run/gpu-lab-pool/control.sock')
 s.sendall(b'{"method":"status"}\\n')
 r=json.loads(s.makefile('rb').readline())
 assert r['ok'] and r['result']['scheduler_version']==4
 assert all(r['result']['gpus'].get(k,{}).get('available') for k in ['0','1'])
'''
        for attempt in range(40):
            result = subprocess.run(["runuser", "-u", account.pw_name, "--", "/usr/bin/python3", "-c", probe],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("Dispatcher readiness failed: "+result.stderr[-1000:])
        command("systemctl", "enable", service)
    except Exception:
        if not was_active:
            subprocess.run(["systemctl", "stop", service], capture_output=True)
        for path, previous in originals.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, *previous)
        command("systemctl", "daemon-reload")
        if was_active:
            command("systemctl", "restart", service)
        raise

    # Activate new clients only after a successful owner-UID socket probe.
    config["dispatcher_socket"] = "/run/gpu-lab-pool/control.sock"
    atomic_write(config_path, json.dumps(config, indent=2)+"\n")
    command("systemctl", "restart", "gpu-lab-dashboard.service")
    after = {unit: command("systemctl", "show", "--value", "-p", "MainPID", unit) for unit in units}
    if before != after:
        raise RuntimeError("GPU worker PID changed unexpectedly: "+str({"before":before,"after":after}))
    print(json.dumps({"dispatcher":"active and enabled at boot", "worker_pids_unchanged":after,
                      "backup":str(backup), "dashboard":"restarted; reopen GPU Lab to authenticate",
                      "clients":"CLI updated; reconnect previously running MCP adapters"}, indent=2))


if __name__ == "__main__":
    main()
