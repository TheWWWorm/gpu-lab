# Installation

## Supported starting point

This release supports a real Linux x86_64 host with Debian/Ubuntu NVIDIA file locations, two NVIDIA GPUs, one ordinary service account, Python 3.10+, systemd 254+, and unified cgroup v2. It has been exercised on Ubuntu 24.04 with NVIDIA 580 and RTX 3090/3090 Ti. Other models or distributions, including pairs of identical GPUs, need hardware validation. PCI domain 0 is currently required by the device resolver.

The worker uses systemd's [`DelegateSubgroup`](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html#DelegateSubgroup=), introduced in version 254. A container, WSL session or ordinary virtual machine is not an equivalent tested environment. Single-GPU and more-than-two-GPU configurations are not supported by the complete installer/dashboard in this release.

Install the NVIDIA driver through your distribution's supported method first. Both GPUs must appear in `nvidia-smi -L`, with the NVIDIA Xorg module, OpenGL, CUDA driver library, Vulkan ICD, and DRM render nodes available. GPU Lab does not install or replace the driver.

Typical Ubuntu dependencies:

```sh
sudo apt-get install python3 python3-pil xserver-xorg-core xauth \
  x11-apps x11-utils dbus-daemon nvidia-settings libx11-6 libxtst6 \
  libvulkan1 mesa-vulkan-drivers
```

For development, also install `python3-venv`, `make`, `git`, and Node.js. A CUDA toolkit is not required for the included driver-level probe. Wine and Godot are optional, user-installed test applications.

## Preview and check

Use the system Python that the services will use, not an unrelated virtual environment. From the repository:

```sh
nvidia-smi --query-gpu=index,uuid,name --format=csv
/usr/bin/python3 setup_gpu_lab.py --user "$USER" --gpu0 GPU-... --gpu1 GPU-...
/usr/bin/python3 setup_gpu_lab.py --user "$USER" --gpu0 GPU-... --gpu1 GPU-... --check
```

Replace `GPU-...` with complete UUIDs. Slots are labels chosen by these arguments, not a promise that NVIDIA's enumeration indices remain fixed. Put the monitor-driving GPU in slot 0 if it should receive the larger default desktop reserve. UUIDs are resolved again at boot.

The default prints the four installation commands and changes nothing. `--check` inspects the account, systemd/cgroups, dependencies, GPU identities and DRM nodes without starting displays or altering services. No network downloads are performed by either mode.

## Apply to a fresh host

```sh
sudo /usr/bin/python3 setup_gpu_lab.py --user "$USER" --gpu0 GPU-... --gpu1 GPU-... --apply
```

`--apply` requires root, refuses existing installation/state directories, and checks that the reserved display numbers are unused. It installs two workers, then the dashboard and shared dispatcher. It starts GPU Lab's own virtual displays as part of installation; it does not restart GDM, change monitor settings, or switch the physical console. Review the plan and [security limits](../SECURITY.md) before administering another workstation.

Installed locations:

| Location | Purpose |
| --- | --- |
| `/opt/gpu-lab` | Root-owned application code and docs |
| `/etc/gpu-lab` | Worker and pool configuration |
| `/etc/systemd/system/gpu-lab*.service` | Boot service units |
| `/usr/local/bin/gpu-lab` | CLI and stdio MCP entry point |
| `/usr/local/bin/gpu-lab-dashboard-open` | Authenticated dashboard launcher |
| `/var/lib/gpu-lab`, `/var/lib/gpu-lab-gpu0` | Worker job records and artifacts |
| `/var/lib/gpu-lab-pool` | Admission lock and durable shared queue |
| `/run/gpu-lab*` | Private sockets, tokens and display sessions |

The helper installers remain available for maintenance, but the base and pool helpers refuse to replace existing workers/configuration. The wrapper is a fresh-install tool, not a general upgrade manager. If a step fails, earlier installed components remain for diagnosis; do not delete state or blindly rerun helpers. Inspect their journal and follow [administration](administration.md).

## Verify

As the ordinary account:

```sh
gpu-lab status
systemctl status gpu-lab-dispatch.service gpu-lab.service gpu-lab-gpu0.service
python3 scripts/smoke_test.py
python3 scripts/smoke_test.py --probe
gpu-lab-dashboard-open
```

Status should report `mode: "shared-queue"`, `scheduler_version: 4`, and both workers available. The smoke test uses only jobs it submits. The optional probe checks an actual OpenGL readback and Vulkan/CUDA device enumeration inside the assigned worker.

Connect clients with [the registration guide](clients.md). Fresh installation on a separate clean machine has not been exercised as part of this source preparation; the installed workstation, dry-run plan, prerequisite checks, installer guards and live job path have been checked independently.
