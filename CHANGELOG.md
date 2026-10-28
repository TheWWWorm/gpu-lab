# Changelog

## Unreleased

- Update the pinned GitHub checkout and Python setup actions to 7.0.1 and 7.1.0.
- Preserve the Python 3.10-compatible Pillow requirement and avoid unnecessary minimum-version increases from Dependabot.
- Check the newest compatible Pillow on Python 3.10–3.13 and the supported Pillow 10.0 minimum on Python 3.10 in CI.

## 4.0.0 — initial public source release

- Durable shared queue with GPU selection at dispatch, idle-GPU preference, and a 20-second settling period before sharing.
- Up to three isolated graphical jobs per GPU, load/VRAM admission, and heavy-job exclusivity among GPU Lab jobs.
- Stable job IDs, conservative recovery from lost worker replies, queue persistence, logs, input, screenshots and descendant cleanup.
- Local dashboard with GPU load/memory, shared queue positions, waiting reasons and selected display previews.
- CLI and stdio MCP interface for Codex, Claude Code and other clients.
- Read-only installation preview, prerequisite checks, explicit fresh installation, and selective client registration.
- Portable tests, documentation, source archive tooling, CI, and Apache-2.0 licensing.

Validation includes CPU-only regression tests and live tests against an existing two-GPU Ubuntu installation. Fresh installation on a separate clean machine has not yet been validated. Single-GPU dashboards, more than two GPUs, AMD/Intel hardware and non-Linux hosts are outside this release's supported scope.
