# Preparing a source release

1. Update `VERSION`, the MCP `serverInfo.version` in `gpu_lab.py`, and `CHANGELOG.md` together.
2. Run `make test` and `make check` from a clean checkout. Run opt-in hardware tests on an installed workstation and record their platform. Validate fresh installation separately before claiming that coverage.
3. Check the tracked file list for private configuration, device UUIDs, user paths, runtime tokens, job logs, or sensitive screenshots. Generated artifacts belong in ignored `.artifacts/` or `dist/`.
4. Commit the reviewed source. `make dist` refuses uncommitted changes and builds ZIP and tar.gz archives from the committed tree, with SHA-256 checksums. Every archive includes LICENSE and NOTICE.
5. Extract an archive into a separate directory and rerun the checks. Confirm the setup preview and client registration preview work there.
6. When publishing is explicitly authorized, create the remote repository, push the reviewed commit, and optionally tag the release. Enable private security reporting and review repository permissions before advertising the reporting path.

This tooling prepares local source archives. It does not create a remote, upload code, publish releases, or include the user's installed configuration/state. Runtime dependencies are installed separately; no NVIDIA, Xorg, browser or engine binaries are distributed.
