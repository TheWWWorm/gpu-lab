#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Install only the local dashboard. Never stop or restart GPU workers."""
import json
import os
from pathlib import Path
import pwd
import subprocess
from install import write


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this one-time installer with sudo")
    config = json.loads(Path("/etc/gpu-lab/config.json").read_text())
    account = pwd.getpwnam(config["user"])
    if account.pw_uid == 0:
        raise SystemExit("The dashboard must run as the workstation user")
    source = Path(__file__).resolve().parent
    dest = Path("/opt/gpu-lab")
    if not (dest/"gpu_lab.py").is_file() or dest.is_symlink():
        raise SystemExit("Install GPU Lab workers first")
    for name in ["dashboard_server.py", "dashboard_open.py", "install_dashboard.py", "README.md"]:
        write(dest/name, (source/name).read_text(), 0o755 if name.endswith(".py") else 0o644)
    for name in ["index.html", "app.js", "style.css", "icon.svg"]:
        write(dest/"dashboard"/name, (source/"dashboard"/name).read_text())
    write("/usr/local/bin/gpu-lab-dashboard-open", '#!/bin/sh\nexec /usr/bin/python3 /opt/gpu-lab/dashboard_open.py "$@"\n', 0o755)
    write("/usr/share/applications/gpu-lab.desktop", '''[Desktop Entry]
Type=Application
Name=GPU Lab
Comment=View GPU jobs, queues, load and virtual displays
Exec=/usr/local/bin/gpu-lab-dashboard-open
Icon=/opt/gpu-lab/dashboard/icon.svg
Terminal=false
Categories=System;Monitor;
Keywords=GPU;NVIDIA;Jobs;Queue;Monitor;
StartupNotify=false
''')
    write("/etc/systemd/system/gpu-lab-dashboard.service", f'''[Unit]
Description=GPU Lab local activity dashboard
After=gpu-lab.service gpu-lab-gpu0.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User={account.pw_name}
Group={account.pw_gid}
WorkingDirectory=/opt/gpu-lab
ExecStart=/usr/bin/python3 /opt/gpu-lab/dashboard_server.py
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
RuntimeDirectory=gpu-lab-dashboard
RuntimeDirectoryMode=0700
UMask=0077
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/run/gpu-lab-dashboard /var/lib/gpu-lab /var/lib/gpu-lab-gpu0
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
TasksMax=128
Nice=5
CPUWeight=25
MemoryMax=512M

[Install]
WantedBy=multi-user.target
''')
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "gpu-lab-dashboard.service"], check=True)
    subprocess.run(["systemctl", "restart", "gpu-lab-dashboard.service"], check=True)
    subprocess.run(["update-desktop-database", "/usr/share/applications"], check=False, capture_output=True)
    print("GPU Lab dashboard installed and enabled at boot. Open GPU Lab from Applications.")
    print("Only gpu-lab-dashboard.service was started/restarted; GPU workers were left running.")


if __name__ == "__main__":
    main()
