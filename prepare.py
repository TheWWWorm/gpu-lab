#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Resolve the configured GPU UUID at boot; create root-owned device aliases."""
import csv
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


def resolve(config):
    output = subprocess.check_output([
        "/usr/bin/nvidia-smi", "--query-gpu=index,uuid,name,pci.bus_id",
        "--format=csv,noheader,nounits"], text=True, timeout=20)
    rows = [[s.strip() for s in r] for r in csv.reader(output.splitlines())]
    found = [r for r in rows if r[1] == config["gpu_uuid"]]
    if len(found) != 1:
        raise RuntimeError("Configured GPU UUID is unavailable; refusing to select another GPU")
    index, uuid, name, bus = found[0]
    domain, busnum, slot = bus.lower().split(":")
    if int(domain, 16) != 0:
        raise RuntimeError("This installation supports PCI domain 0 only")
    dev, func = slot.split(".")
    pci = f"{int(domain,16):04x}:{busnum}:{dev}.{func}"
    info = Path(f"/proc/driver/nvidia/gpus/{pci}/information").read_text()
    if uuid not in info:
        raise RuntimeError("NVIDIA device identity mismatch")
    minor = re.search(r"Device Minor:\s*(\d+)", info).group(1)
    cards = [p for p in Path("/sys/class/drm").glob("card[0-9]*")
             if "-" not in p.name and (p/"device").resolve().name == pci]
    renders = [p for p in Path("/sys/class/drm").glob("renderD*")
               if (p/"device").resolve().name == pci]
    if len(cards) != 1 or len(renders) != 1:
        raise RuntimeError("Cannot uniquely resolve GPU DRM devices")
    vendor = (cards[0]/"device/vendor").read_text().strip().removeprefix("0x")
    device = (cards[0]/"device/device").read_text().strip().removeprefix("0x")
    return {"index": int(index), "uuid": uuid, "name": name, "pci": pci,
            "bus_id": f"PCI:{int(busnum,16)}:{int(dev,16)}:{int(func,16)}",
            "vendor_device": f"{vendor}:{device}",
            "devices": {"gpu": f"/dev/nvidia{minor}", "card": f"/dev/dri/{cards[0].name}",
                        "render": f"/dev/dri/{renders[0].name}"}}


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    dest = Path(sys.argv[2])
    if os.geteuid() != 0:
        raise SystemExit("This boot preparation step must run as root")
    subprocess.run(["/sbin/modprobe", "nvidia_uvm"], check=True)
    for attempt in range(20):
        try:
            gpu = resolve(config)
            break
        except (RuntimeError, subprocess.SubprocessError):
            if attempt == 19:
                raise
            time.sleep(1)
    dest.mkdir(mode=0o755, parents=True, exist_ok=True)
    aliases = Path(config.get("device_aliases", "/dev/gpu-lab"))
    if aliases.parent != Path("/dev") or not re.fullmatch(r"gpu-lab(?:-[a-z0-9]+)?", aliases.name):
        raise RuntimeError("Device alias directory must be /dev/gpu-lab or /dev/gpu-lab-NAME")
    if aliases.is_symlink():
        raise RuntimeError("Refusing a symlink device alias directory")
    aliases.mkdir(mode=0o755, exist_ok=True)
    if aliases.stat().st_uid != 0:
        raise RuntimeError("Device aliases must be owned by root")
    for name, path in gpu["devices"].items():
        if not stat.S_ISCHR(os.stat(path).st_mode):
            raise RuntimeError(f"Not a character device: {path}")
        target = aliases/name
        target.unlink(missing_ok=True)
        target.symlink_to(path)
    tmp = dest/"gpu.json.tmp"
    tmp.write_text(json.dumps(gpu, indent=2)+"\n")
    tmp.chmod(0o644)
    tmp.replace(dest/"gpu.json")
    print(json.dumps(gpu))


if __name__ == "__main__":
    main()
