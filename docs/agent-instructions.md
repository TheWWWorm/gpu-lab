<!-- gpu-lab:start -->
## Shared GPU graphical testing on this workstation

Use `gpu_lab` for isolated graphical, Vulkan, OpenGL, CUDA, Godot, Wine/DXVK
and VKD3D GPU tests. **Leave `gpu` omitted or set it to `"auto"`** so jobs share
both GPUs. Do not pin every test to GPU1. Each GPU supports up to three
concurrent jobs, each with its own virtual display. Recent GPU load, free VRAM,
startup reservations and queue order control when additional jobs can start.
Automatic jobs wait in a shared queue and choose a GPU when capacity becomes
available. Idle GPUs are preferred before sharing; a 20-second settling period
lets a new job's GPU load become visible. Keep the returned job ID while
`gpu_slot` changes from `"auto"` to its assignment. Running jobs are not moved.
Pin `gpu: "0"` or `gpu: "1"` only for a device-specific requirement. CPU-only
builds, imports and checks run normally outside the GPU queues.

Leave `workload` omitted/`"auto"` for ordinary tests. Use `"light"` for small
UI checks, and `"heavy"` for benchmarks or sustained heavy work that should run
alone among GPU Lab jobs on its assigned GPU. Do not mark everything heavy.
Supply `vram_mb` when the peak memory requirement is known (MiB; an estimate,
not a quota). Defaults are auto 2048, light 512, heavy 8192. Queued jobs expose
`waiting_reason`; wait normally instead of overriding display/GPU variables.

The shared dispatcher and both virtual-display workers start at boot. Use `gpu_submit` with an argv array
and absolute cwd, then `gpu_job`/`gpu_logs`; use your running job ID for
screenshots/input. The assigned physical GPU appears as logical GPU 0 inside
each job. GPU0 also drives the monitors, so heavy tests can affect desktop
responsiveness even though their windows are on a separate virtual display.

CLI fallback, available immediately in any terminal:
`/usr/local/bin/gpu-lab run --gpu auto --cwd /absolute/project --timeout 300 -- executable arguments`

Use `--workload heavy --vram-mb 8192` when appropriate. `gpu_status` must report
`mode: "shared-queue"` and `scheduler_version: 4` for the current dispatcher.
Older MCP sessions keep their loaded routing code until reconnected: use the
CLI immediately for shared-queue scheduling, then reconnect MCP when convenient.

Do not start a separate Xorg/Xvfb, switch virtual consoles, change monitor
settings, reset GPUs, or restart GDM to run a test. Use the physical desktop
only when the user requests it. Do not override the service's display/GPU
environment. Put files in the project/home, not the caller's `/tmp` (the service
has its own `/tmp`). For Godot visual tests use `--display-driver x11` and
`--audio-driver Dummy`; the display is already hardware accelerated. Prefer
`--max-fps 30` for visual inspections so an uncapped render loop does not
consume all GPU capacity. Benchmarks should use `heavy` and their intended
frame-rate settings. For Wine,
use a dedicated WINEPREFIX. Full usage: `/opt/gpu-lab/README.md`.
<!-- gpu-lab:end -->
