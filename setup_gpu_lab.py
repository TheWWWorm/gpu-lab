#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Plan, check or install GPU Lab on a fresh two-GPU Linux workstation."""
import argparse
import ctypes.util
import grp
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shlex
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
REQUIRED_FILES = (
    "/usr/lib/xorg/Xorg", "/usr/bin/xauth", "/usr/bin/xdpyinfo", "/usr/bin/xwd",
    "/usr/bin/xwininfo", "/usr/bin/nvidia-settings", "/usr/bin/nvidia-smi",
    "/usr/bin/dbus-run-session", "/usr/bin/systemctl", "/usr/bin/systemd-analyze",
    "/usr/share/vulkan/icd.d/nvidia_icd.json",
    "/usr/share/vulkan/implicit_layer.d/VkLayer_MESA_device_select.json",
)
INSTALL_MARKERS = (
    "/etc/gpu-lab", "/opt/gpu-lab", "/etc/systemd/system/gpu-lab.service",
    "/etc/systemd/system/gpu-lab-gpu0.service", "/var/lib/gpu-lab",
    "/var/lib/gpu-lab-gpu0", "/var/lib/gpu-lab-pool",
    "/usr/local/bin/gpu-lab", "/usr/local/bin/gpu-lab-dashboard-open",
    "/usr/share/applications/gpu-lab.desktop", "/dev/gpu-lab", "/dev/gpu-lab-gpu0",
)


def plan(user, gpu0, gpu1):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9-]*", user):
        raise ValueError("Supply a normal Linux account name")
    for value in [gpu0, gpu1]:
        if not re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
            raise ValueError("Supply complete GPU UUIDs from nvidia-smi -L")
    if gpu0.lower() == gpu1.lower():
        raise ValueError("Choose two different GPU UUIDs")
    return [
        ["/usr/bin/python3", str(SOURCE/"install.py"), "--user", user, "--gpu-uuid", gpu1],
        ["/usr/bin/python3", str(SOURCE/"install_pool.py"), "--gpu0-uuid", gpu0],
        ["/usr/bin/python3", str(SOURCE/"install_dashboard.py")],
        ["/usr/bin/python3", str(SOURCE/"install_dispatch.py")],
    ]


def prerequisites(user, gpu0, gpu1):
    errors = []
    if sys.version_info < (3, 10):
        errors.append("Python 3.10 or newer is required")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        errors.append("This release supports Linux x86_64 with Debian/Ubuntu NVIDIA paths")
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        errors.append("A unified cgroup v2 hierarchy is required")
    if not Path("/run/systemd/system").is_dir():
        errors.append("systemd must be the host service manager")
    try:
        version = subprocess.check_output(["systemctl", "--version"], text=True, timeout=5)
        if int(version.split()[1]) < 254:
            errors.append("systemd 254+ is required for DelegateSubgroup")
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        errors.append("Could not determine systemd version")
    try:
        account = pwd.getpwnam(user)
        if account.pw_uid == 0 or not Path(account.pw_dir).is_dir():
            errors.append("The service account must be non-root with an existing home directory")
    except KeyError:
        errors.append("The selected service account does not exist")
    for group in ["video", "render"]:
        try:
            grp.getgrnam(group)
        except KeyError:
            errors.append("Missing system group: "+group)
    errors.extend("Missing prerequisite: "+p for p in REQUIRED_FILES if not Path(p).is_file())
    if importlib.util.find_spec("PIL") is None:
        errors.append("Install Pillow for /usr/bin/python3 (Ubuntu: python3-pil)")
    for library in ["X11", "Xtst", "GL", "vulkan", "cuda"]:
        if not ctypes.util.find_library(library):
            errors.append("Missing runtime library: "+library)
    if Path("/usr/bin/nvidia-smi").is_file():
        from prepare import resolve
        for slot, uuid in [("0", gpu0), ("1", gpu1)]:
            try:
                resolve({"gpu_uuid": uuid})
            except (OSError, ValueError, RuntimeError, AttributeError, subprocess.SubprocessError) as exc:
                errors.append(f"GPU {slot}: {exc}")
    return errors


def ensure_fresh():
    present = [p for p in INSTALL_MARKERS if Path(p).exists()]
    if present:
        raise RuntimeError("Existing installation/state found; no changes made. See docs/administration.md.\n"+"\n".join(present))
    # Displays must be free. Never replace another X server or remove its lock.
    for number in [98, 99, 108, 109, 118, 119]:
        if Path(f"/tmp/.X{number}-lock").exists() or Path(f"/tmp/.X11-unix/X{number}").exists():
            raise RuntimeError(f"Display :{number} is occupied; no changes made")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="ordinary account that owns all jobs")
    parser.add_argument("--gpu0", required=True, help="UUID; slot 0 uses extra desktop headroom")
    parser.add_argument("--gpu1", required=True, help="UUID of the second GPU")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only prerequisite and GPU checks")
    mode.add_argument("--apply", action="store_true", help="install and start services (requires sudo)")
    mode.add_argument("--dry-run", action="store_true", help="print the plan only (the default)")
    args = parser.parse_args(argv)
    try:
        commands = plan(args.user, args.gpu0, args.gpu1)
        if not args.check and not args.apply:
            print("Fresh installation plan; no files or services changed:")
            for command in commands:
                print(shlex.join(command))
            print("Run --check for prerequisites, then --apply with sudo to install.")
            return 0
        errors = prerequisites(args.user, args.gpu0, args.gpu1)
        if errors:
            print(json.dumps({"ready": False, "errors": errors}, indent=2))
            return 1
        if args.check:
            print(json.dumps({"ready": True, "checks": "systemd, cgroups, account, dependencies and GPU identities"}))
            return 0
        if os.geteuid() != 0:
            raise RuntimeError("Use sudo for the one-time --apply installation")
        ensure_fresh()
        for command in commands:
            print("Running:", shlex.join(command), flush=True)
            subprocess.run(command, check=True)
        for folder in ["docs", "examples"]:
            for file in (SOURCE/folder).rglob("*"):
                if file.is_file() and not file.is_symlink():
                    target = Path("/opt/gpu-lab")/file.relative_to(SOURCE)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(file.read_bytes())
                    target.chmod(0o644)
        print("GPU Lab is installed. As the ordinary user, run gpu-lab status and python3 register_clients.py --help.")
        return 0
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
