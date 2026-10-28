#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Add the GPU0 worker and pooled routing without interrupting GPU1 jobs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from install import write
from prepare import resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu0-uuid", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this installer once with sudo")
    if Path("/etc/gpu-lab/pool.json").exists() or Path("/etc/gpu-lab/gpu0.json").exists():
        raise SystemExit("The GPU pool is already installed; refusing to overwrite its configuration")
    source = Path(__file__).resolve().parent
    gpu1 = json.loads(Path("/etc/gpu-lab/config.json").read_text())
    if args.gpu0_uuid == gpu1["gpu_uuid"]:
        raise SystemExit("Pool workers must use different GPUs")
    config = gpu1 | {"gpu_uuid": args.gpu0_uuid, "display": ":99", "device_aliases": "/dev/gpu-lab-gpu0",
                     "displays": [":99", ":109", ":119"], "max_concurrent": 3,
                     "memory_headroom_mb": 2048, "share_utilization_limit": 65}
    resolve(config)
    active = subprocess.run(["systemctl", "is-active", "--quiet", "gpu-lab-gpu0.service"]).returncode == 0
    if active:
        raise SystemExit("GPU0 worker already running; stop it only when its queue is empty before reinstalling")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # Back up installed source before updating. Running GPU1 daemon code stays
    # loaded; its child entry point and worker protocol remain compatible.
    dest = Path("/opt/gpu-lab")
    backup = dest/("before-pool-"+stamp)
    backup.mkdir(mode=0o700)
    names = ["gpu_schedule.py", "gpu_pool.py", "gpu_lab.py", "x11.py", "prepare.py", "install_pool.py", "README.md"]
    for name in names:
        if (dest/name).exists():
            shutil.copy2(dest/name, backup/name)
        temp = dest/(name+".new")
        write(temp, (source/name).read_text(), 0o755 if name.endswith(".py") else 0o644)
        temp.replace(dest/name)
    write("/etc/gpu-lab/gpu0.json", json.dumps(config, indent=2)+"\n")
    prep = Path("/etc/systemd/system/gpu-lab-device.service").read_text()
    prep = prep.replace("gpu-lab-device.service", "gpu-lab-gpu0-device.service")
    prep = prep.replace("gpu-lab.service", "gpu-lab-gpu0.service")
    prep = prep.replace("gpu-lab-devices", "gpu-lab-gpu0-devices")
    prep = prep.replace("/etc/gpu-lab/config.json", "/etc/gpu-lab/gpu0.json")
    write("/etc/systemd/system/gpu-lab-gpu0-device.service", prep)
    unit = Path("/etc/systemd/system/gpu-lab.service").read_text()
    unit = unit.replace("Description=GPU1", "Description=GPU0")
    unit = unit.replace("gpu-lab-device.service", "gpu-lab-gpu0-device.service")
    unit = unit.replace("RuntimeDirectory=gpu-lab\n", "RuntimeDirectory=gpu-lab-gpu0\n")
    unit = unit.replace("StateDirectory=gpu-lab\n", "StateDirectory=gpu-lab-gpu0\n")
    unit = unit.replace("/dev/gpu-lab/", "/dev/gpu-lab-gpu0/")
    unit = unit.replace("gpu_lab.py serve\n", "gpu_lab.py serve --config /etc/gpu-lab/gpu0.json "
                        "--gpu /run/gpu-lab-gpu0-devices/gpu.json --runtime /run/gpu-lab-gpu0 "
                        "--state /var/lib/gpu-lab-gpu0\n")
    write("/etc/systemd/system/gpu-lab-gpu0.service", unit)
    subprocess.run(["systemd-analyze", "verify", "/etc/systemd/system/gpu-lab-gpu0-device.service",
                    "/etc/systemd/system/gpu-lab-gpu0.service"], check=True)
    state = Path("/var/lib/gpu-lab-pool")
    state.mkdir(mode=0o700, exist_ok=True)
    os.chown(state, gpu1["uid"], -1)
    state.chmod(0o700)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "gpu-lab-gpu0-device.service"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "gpu-lab-gpu0.service"], check=True)
    # Wait for X startup; do not advertise the second GPU prematurely.
    for _ in range(100):
        if Path("/run/gpu-lab-gpu0/control.sock").exists():
            break
        time.sleep(.1)
    else:
        raise SystemExit("GPU0 worker did not become ready; inspect its journal. GPU1 was left running.")
    pool = {"lock_path": "/var/lib/gpu-lab-pool/schedule.lock", "workers": {
        "0": {"gpu_uuid": config["gpu_uuid"], "socket": "/run/gpu-lab-gpu0/control.sock"},
        "1": {"gpu_uuid": gpu1["gpu_uuid"], "socket": "/run/gpu-lab/control.sock"}}}
    write("/etc/gpu-lab/pool.json.new", json.dumps(pool, indent=2)+"\n")
    Path("/etc/gpu-lab/pool.json.new").replace("/etc/gpu-lab/pool.json")
    print("Enabled both virtual GPU workers. New CLI/MCP adapters now default to automatic routing.")
    print("Existing GPU1 jobs and their IDs were preserved. Reconnect MCP clients for the new tool schema.")


if __name__ == "__main__":
    main()
