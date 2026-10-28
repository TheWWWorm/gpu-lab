# Security and operating limits

GPU Lab runs cooperative local test programs as one configured, non-root workstation account. Anyone who can act as that account can submit programs with that account's file and network access. The service is not a sandbox for hostile code and does not provide isolation between mutually untrusted users or agents.

Worker device policies restrict ordinary GPU access to the assigned card. Per-job Xauthority, D-Bus sessions, runtime directories and cgroups prevent accidental interaction between test displays and support descendant cleanup. They do not make same-account processes a security boundary. A workload can still consume CPU, system RAM, storage or network resources. VRAM hints are estimates, not enforced quotas. The service does not schedule workloads launched outside GPU Lab.

A card connected to physical monitors shares hardware with the desktop. Heavy GPU or RAM pressure, driver failures, and kernel faults can affect the whole workstation despite separate test displays. GPU Lab does not reset GPUs or restart the desktop to recover a job.

## Access

Control sockets and runtime state are owner-only, and peers are checked by Unix UID. The dashboard is read-only, binds to loopback, checks origins/hosts, and requires a private launch token. Keep it on loopback. Do not expose its HTTP port or Unix sockets through a public reverse proxy. The token grants access to job commands, logs, and previews; do not paste authenticated launch URLs into issues.

Installation requires sudo once. Runtime jobs, the dispatcher, the dashboard and MCP adapters run unprivileged. No administrator password is stored, and no general passwordless sudo entry is installed.

## Reporting a vulnerability

Use the hosting repository's private vulnerability reporting if it is enabled. If a private channel is unavailable, open an issue requesting a private contact without disclosing exploit details, secrets, or sensitive logs. Include affected versions, expected impact and a minimal reproduction once a private channel is established.

Until a public repository and maintainer contact are configured, report findings directly to the person who supplied your copy. This source checkout does not claim a hosted reporting endpoint or a response SLA.
