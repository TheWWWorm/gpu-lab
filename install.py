#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Install the prepared GPU Lab code and systemd units. No network downloads."""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

from prepare import resolve


def write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"Refusing to overwrite symlink: {path}")
    path.write_text(text)
    path.chmod(mode)
    os.chown(path, 0, 0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--user", required=True)
    p.add_argument("--gpu-uuid", required=True)
    args = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this installer once with sudo")
    if Path("/etc/gpu-lab/config.json").exists() or Path("/etc/systemd/system/gpu-lab.service").exists():
        raise SystemExit("GPU Lab is already installed. Fresh setup refuses to replace or stop an existing worker; see docs/administration.md")
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0:
        raise SystemExit("Test jobs must run as a normal user")
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.user):
        raise SystemExit("Unsupported account name")
    for path in ["/usr/lib/xorg/Xorg", "/usr/bin/xauth", "/usr/bin/xdpyinfo", "/usr/bin/xwd",
                 "/usr/bin/xwininfo", "/usr/bin/nvidia-settings", "/usr/bin/nvidia-smi",
                 "/usr/bin/dbus-run-session"]:
        if not Path(path).is_file():
            raise SystemExit("Missing prerequisite: "+path)
    import PIL.Image  # Supplied by the documented python3-pil dependency.
    config = {"user": args.user, "uid": account.pw_uid, "home": account.pw_dir,
              "gpu_uuid": args.gpu_uuid, "display": ":98", "size": [2880, 1800],
              "displays": [":98", ":108", ":118"], "max_concurrent": 3,
              "memory_headroom_mb": 1024, "share_utilization_limit": 75}
    gpu = resolve(config)
    if gpu["uuid"] != args.gpu_uuid:
        raise SystemExit("GPU identity mismatch")
    source = Path(__file__).resolve().parent
    dest = Path("/opt/gpu-lab")
    dest.mkdir(mode=0o755, exist_ok=True)
    if dest.is_symlink():
        raise SystemExit("Refusing symlink installation directory")
    for name in ["gpu_schedule.py", "gpu_pool.py", "gpu_lab.py", "x11.py", "prepare.py", "install.py", "README.md", "LICENSE", "NOTICE", "VERSION"]:
        write(dest/name, (source/name).read_text(), 0o755 if name.endswith(".py") else 0o644)
    write("/etc/gpu-lab/config.json", json.dumps(config, indent=2)+"\n")
    write("/usr/local/bin/gpu-lab", '#!/bin/sh\nexec /usr/bin/python3 /opt/gpu-lab/gpu_lab.py "$@"\n', 0o755)
    write("/etc/systemd/system/gpu-lab-device.service", '''[Unit]
Description=Resolve the dedicated GPU Lab device by UUID
After=systemd-udev-settle.service
Before=gpu-lab.service

[Service]
Type=oneshot
RemainAfterExit=yes
RuntimeDirectory=gpu-lab-devices
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 /opt/gpu-lab/prepare.py /etc/gpu-lab/config.json /run/gpu-lab-devices
TimeoutStartSec=90
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/run/gpu-lab-devices
''')
    write("/etc/systemd/system/gpu-lab.service", f'''[Unit]
Description=GPU1 virtual display and agent test queue
Requires=gpu-lab-device.service
After=gpu-lab-device.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User={args.user}
Group={account.pw_gid}
SupplementaryGroups=video render
RuntimeDirectory=gpu-lab
RuntimeDirectoryMode=0700
StateDirectory=gpu-lab
StateDirectoryMode=0700
WorkingDirectory=/opt/gpu-lab
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /opt/gpu-lab/gpu_lab.py serve
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=no
PrivateTmp=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
DevicePolicy=closed
DeviceAllow=/dev/nvidiactl rw
DeviceAllow=/dev/nvidia-modeset rw
DeviceAllow=/dev/nvidia-uvm rw
DeviceAllow=/dev/nvidia-uvm-tools rw
DeviceAllow=/dev/gpu-lab/gpu rw
DeviceAllow=/dev/gpu-lab/card rw
DeviceAllow=/dev/gpu-lab/render rw
InaccessiblePaths=-/run/user/{account.pw_uid}/gdm -/run/user/{account.pw_uid}/wayland-0 -/run/user/{account.pw_uid}/wayland-1
Delegate=yes
DelegateSubgroup=supervisor
TasksMax=2048
Nice=5
CPUWeight=50
IOWeight=50

[Install]
WantedBy=multi-user.target
''')
    subprocess.run(["systemd-analyze", "verify", "/etc/systemd/system/gpu-lab-device.service",
                    "/etc/systemd/system/gpu-lab.service"], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "gpu-lab-device.service"], check=True)
    subprocess.run(["systemctl", "reset-failed", "gpu-lab.service"], check=False)
    subprocess.run(["systemctl", "enable", "--now", "gpu-lab.service"], check=True)
    print("Installed GPU Lab for", args.user, "on", gpu["name"], gpu["uuid"])
    print("The service starts at boot; no user login or sudo is needed for test jobs.")


if __name__ == "__main__":
    main()
