# CLI and MCP usage

## Jobs

```sh
gpu-lab status
gpu-lab run --cwd "$PWD" --timeout 120 -- executable arguments
gpu-lab submit --cwd "$PWD" --timeout 300 -- executable arguments
gpu-lab job JOB_ID
gpu-lab logs JOB_ID
gpu-lab screenshot JOB_ID --output "$PWD/screenshot.png"
gpu-lab windows JOB_ID
gpu-lab input JOB_ID '[{"type":"key","key":"Return"}]'
gpu-lab cancel JOB_ID
```

`run` waits and returns the job's exit status; Ctrl+C requests cancellation of that job. `submit` returns immediately. Keep the returned ID for all subsequent operations. Automatic submissions initially have `gpu_slot: "auto"`; after dispatch it becomes `"0"` or `"1"`. The public ID does not change. `waiting_reason` explains a delay.

Use an absolute working directory. Files must be in the project/home, since the service has a private `/tmp`. Each job gets its own display automatically; never replace the service's `DISPLAY`, GPU-selection or session variables. Standard input is closed. Application-specific environment values can be passed with `--env KEY=VALUE`; they are saved privately and omitted from public job metadata. Avoid secrets in command arguments and labels, which are visible in status and the dashboard.

## Workload hints

| Hint | Intended use | Default VRAM estimate |
| --- | --- | --- |
| `auto` | Ordinary tests; the default | 2048 MiB |
| `light` | Small UI and smoke checks | 512 MiB |
| `heavy` | Benchmarks or sustained heavy work; runs alone among Lab jobs on its GPU | 8192 MiB |

Use `--vram-mb` when you know a better peak estimate. Hints do not bypass admission checks or impose hardware quotas. Select `--gpu 0` or `--gpu 1` only for a device-specific test. Leave it omitted or `auto` otherwise.

```sh
gpu-lab run --workload heavy --vram-mb 8192 --cwd "$PWD" -- benchmark
gpu-lab run --workload light --vram-mb 512 --cwd "$PWD" -- ui-check
```

The assigned physical GPU is logical GPU 0 inside the process. Do not pass an application's GPU index 1 simply because the job was assigned to slot 1.

## Graphical applications

Godot example (replace `godot` with its installed executable path if necessary):

```sh
gpu-lab run --cwd "$PWD" --workload light --timeout 90 -- \
  godot --path "$PWD/examples/godot" --display-driver x11 --audio-driver Dummy \
  --rendering-method forward_plus --max-fps 30 --resolution 800x600 \
  --script "$PWD/examples/godot/smoke.gd"
```

Use a dedicated `WINEPREFIX` in your project/home for Wine. Visual inspections should usually have a frame cap. Performance benchmarks should use `heavy` and their intended frame-rate settings. CPU-only imports and builds can run normally outside the queue.

The display has no window manager. Use application windowed/size options. Input keys use X11 names (`Return`, `Escape`, `Control_L+a`). Click the application's window before typing. Screenshots retain full-resolution PNGs and MCP also returns a scaled preview; input coordinates always use full resolution. Only a running job can receive input or a new screenshot.

## MCP

`gpu_submit` accepts `argv` (an array, without a shell wrapper), `cwd` (absolute), and optional `gpu`, `workload`, `vram_mb`, `timeout_seconds`, `label`, and `env`:

```json
{
  "argv": ["/usr/bin/xmessage", "GPU Lab smoke test"],
  "cwd": "/absolute/project",
  "gpu": "auto",
  "workload": "light",
  "timeout_seconds": 30
}
```

Poll `gpu_job` for completion and use `gpu_logs` for retained output. Every job-scoped tool takes your `job_id`. `gpu_input` also takes an `events` array of `key`, `move`, or `click` objects. Errors are returned as MCP tool errors, without restarting the worker or desktop.

## Dashboard

Open **GPU Lab** from Applications or run `gpu-lab-dashboard-open`. Search/filter jobs, select a row, then use Preview, Logs, or Details. Previews are snapshots approximately every two seconds, not a video stream. Pausing, enlarging, and saving a PNG apply to the selected job. Only the selected preview is captured; temporary preview captures are removed after reading. Captures explicitly saved through CLI/MCP remain in the job's artifacts.

The graph includes all GPU use, including applications outside the queue. Its two-minute history begins when the dashboard service starts. The job list includes all active/queued jobs plus recent history. A shared-queue job shows `Auto` until assigned. Details explain the GPU request and dispatch choice.

After restarting the dashboard, reopen its launcher to obtain a fresh browser session. Its HTTP interface is read-only; submit/cancel/input use CLI or MCP.
