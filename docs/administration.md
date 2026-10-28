# Administration

## Inspect

```sh
gpu-lab status
systemctl status gpu-lab.service gpu-lab-gpu0.service gpu-lab-dispatch.service gpu-lab-dashboard.service
journalctl -u gpu-lab-dispatch.service -b
journalctl -u gpu-lab.service -b
```

Configuration lives in `/etc/gpu-lab/config.json` (slot 1), `gpu0.json` (slot 0), and `pool.json` (routing). It contains machine-specific UUIDs and account details; keep it out of a public repository. Worker settings include displays, resolution, maximum concurrency, utilization thresholds, memory headroom and sampling intervals. `sharing_warmup_seconds` belongs to the pool configuration.

A missing worker/device does not cause a pinned job to silently run elsewhere. Automatic jobs can use another available GPU. Stale telemetry prevents additional sharing. Check `waiting_reason` before deciding a queue is stuck.

## Existing installations and upgrades

The complete setup tool refuses to overwrite an existing installation. This first public release does not provide a general automated upgrade or uninstall manager. Back up `/etc/gpu-lab`, `/opt/gpu-lab` and all three `/var/lib/gpu-lab*` state directories before a planned migration. Stop submissions and let work finish before replacing worker code/configuration. Coordinate with every client using the account.

The included `install_dispatch.py` can update the dispatcher on an existing two-worker installation with the matching worker protocol. It preserves worker PIDs, backs up replaced files and restarts the dispatcher and dashboard. It is intended for the v3/v4 layout described here, not arbitrary older deployments. `install_dashboard.py` updates only the GUI. Neither helper upgrades a worker's loaded code. Reconnect MCP clients after adapter changes; reopen the dashboard launcher after its token rotates.

For maintenance requiring worker replacement, first verify **both the shared queue and worker queues are empty**. Stop the dispatcher to prevent new admissions, recheck each worker, and restart only a worker whose own active/assigned queue is empty. Direct older adapters must also be stopped or disconnected. Do not use a GPU reset or desktop restart for maintenance.

The fresh setup wrapper can leave earlier components installed if a later step fails. Inspect the specific service's journal and saved configuration; do not delete job state or remove display locks to force a retry. A successful prerequisite check is not a guarantee against driver-specific Xorg startup failure.

## Retention

Worker job artifacts live under `/var/lib/gpu-lab/jobs/` and `/var/lib/gpu-lab-gpu0/jobs/`. Shared public records live under `/var/lib/gpu-lab-pool/queue/`. The initial release has no automatic retention policy. Stdout and stderr are bounded to 8 MiB each, retained from the beginning; metadata reports truncation. Explicit screenshots and other job artifacts can still accumulate.

Archive/remove finished records only during coordinated maintenance with the relevant services stopped, and retain public-to-worker mappings for any history you need. Do not remove records for queued or active work. Saved environments may contain secrets; state backups should stay private.

## Disable

The dashboard can be disabled independently:

```sh
sudo systemctl disable --now gpu-lab-dashboard.service
```

To retire the whole installation, stop submissions, let queues drain, then disable the dispatcher and workers:

```sh
sudo systemctl disable --now gpu-lab-dispatch.service gpu-lab.service gpu-lab-gpu0.service
sudo systemctl stop gpu-lab-device.service gpu-lab-gpu0-device.service
```

Stopping workers interrupts their running jobs. Remove the `gpu_lab` MCP entry and marked instruction block from clients when retiring the installation. Keep configuration and state until you have explicitly decided what to retain. There is no destructive purge command.
