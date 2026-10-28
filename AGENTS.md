# Working on GPU Lab

This repository contains privileged installation helpers, unprivileged GPU workers, a dispatcher, a CLI/MCP adapter, and a read-only dashboard. The first public release supports two NVIDIA GPUs on Linux for one trusted workstation account.

- Run `make test` and `make check` for code changes. CPU-only unit tests do not use a GPU.
- For live graphical validation, use an already installed GPU Lab with `gpu=auto` and `workload=light` or `auto`. Use `heavy` for actual benchmarks. Touch only your own job IDs.
- Never reset a GPU, restart GDM, switch virtual consoles, change physical displays, or start a second test X server to validate a patch.
- Do not run an installer merely to test its code. Use its preview/check modes and mocked tests. Installation or worker restarts must be explicitly within the user's task and preserve active work.
- Preserve stable public job IDs, worker ownership, queue order, workload hints, and the rule against resubmitting uncertain requests.
- Keep job environment values private. Do not add real GPU UUIDs, user paths, job logs, screenshots of private projects, auth tokens, client configuration, or diagnostic dumps to Git.
- Keep installation documentation accurate about supported platforms. Do not claim fresh-machine coverage from a mocked installer test.
- Document changes to the protocol, configuration, scheduling policy, or operating requirements in `docs/` and `CHANGELOG.md`.
- New source is Apache-2.0. Keep dependencies explicit; no runtime downloads or broad passwordless sudo rules.
