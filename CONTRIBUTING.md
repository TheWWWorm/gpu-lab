# Contributing

Bug reports and focused improvements are welcome. For bugs, include the release, OS, Python, systemd and NVIDIA driver versions, GPU models, expected behavior, and a minimal reproduction. Remove usernames, full project paths, real GPU UUIDs, job commands containing secrets, and tokens before posting diagnostics.

## Local checks

Use a non-root Linux account:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
make test
make check
```

Install `dbus-daemon` if `dbus-run-session` is unavailable. `make check` also uses Node.js for syntax checking; the dashboard has no build step or npm runtime dependencies. The deterministic scheduler tests simulate both GPUs. Tests for job processes use temporary directories and CPU-only child programs.

Optional hardware checks require a separately installed GPU Lab. `python scripts/smoke_test.py --probe` submits only its own jobs, uses automatic assignment, and cleans up its unfinished work. Use the assigned GPU as logical device 0 inside a job. Optional Godot examples live in `examples/godot/`.

Do not run installation or service restarts as routine tests. The fresh setup preview and prerequisites check are read-only; installer tests mock mutations. A clean-machine installation still needs separate validation on suitable hardware.

## Documentation screenshot

`scripts/render_demo.cjs` renders the actual dashboard using entirely synthetic data. It needs an optional Playwright installation, which is not a runtime dependency. Run it through GPU Lab as a light job. It uses Playwright's default Chromium, or the `GPU_LAB_BROWSER` executable path; `GPU_LAB_PLAYWRIGHT_MODULE` can select an existing Playwright module. The output is `docs/assets/dashboard.png`. Do not replace it with a screenshot containing real private jobs.

## Changes

Keep a pull request focused on observable behavior. Explain the problem, the resulting behavior, the checks performed, and any remaining hardware/platform limitations. Include regression tests for scheduling, process lifecycle, authorization, and installer changes. Update relevant documentation and the changelog.

Never commit runtime state, authentication tokens, private configuration, or personal diagnostic captures. Use synthetic data for dashboard examples. Run `make check` before creating a release archive.

Contributions are provided under Apache-2.0, the project's existing license. Add the SPDX header used by neighboring source files. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
