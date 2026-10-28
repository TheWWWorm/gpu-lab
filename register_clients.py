#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Register GPU Lab in selected MCP clients; preview changes unless --apply."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def backup(path):
    if path.exists():
        target = path.with_name(path.name+".before-gpu-lab-"+str(time.time_ns()))
        shutil.copy2(path, target)
        target.chmod(0o600)


def save_private(path, content, original):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".gpu-lab-")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(content)
        current = path.read_text() if path.exists() else None
        if current != original:
            raise RuntimeError("Client configuration changed during update; retry")
        backup(path)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def register_claude(home, command):
    path = home/".claude.json"
    original = path.read_text() if path.exists() else None
    data = json.loads(original or "{}")
    data.setdefault("mcpServers", {})["gpu_lab"] = {
        "type":"stdio", "command":command, "args":["mcp"], "env":{}}
    save_private(path, json.dumps(data, indent=2)+"\n", original)


def update_instructions(path, block):
    original = path.read_text() if path.exists() else None
    old = original or ""
    start, end = "<!-- gpu-lab:start -->", "<!-- gpu-lab:end -->"
    if start in old:
        a = old.index(start)
        b = old.index(end, a)+len(end)
        updated = old[:a]+block.rstrip()+old[b:]
    else:
        updated = old.rstrip()+"\n\n"+block if old.strip() else block
    save_private(path, updated, original)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex","claude","both"], default="both")
    parser.add_argument("--command", default="/usr/local/bin/gpu-lab", help="absolute installed CLI path")
    parser.add_argument("--with-instructions", action="store_true", help="also add GPU Lab guidance to selected clients' global instructions")
    parser.add_argument("--apply", action="store_true", help="apply the previewed registration as the ordinary user")
    args = parser.parse_args(argv)
    clients = ["codex","claude"] if args.client == "both" else [args.client]
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home/".codex"))).expanduser()
    print("Clients:", ", ".join(clients))
    print("MCP command:", args.command, "mcp")
    print("Update global instructions:", args.with_instructions)
    if not args.apply:
        print("Preview only. Add --apply to register. Existing settings are preserved and backed up.")
        return 0
    if os.geteuid() == 0:
        parser.error("Run client registration as the ordinary user, without sudo")
    if not Path(args.command).is_absolute() or not os.access(args.command, os.X_OK):
        parser.error("Install the GPU Lab CLI first, or supply an executable absolute --command")
    if "codex" in clients and not shutil.which("codex"):
        parser.error("Codex CLI is missing; choose --client claude or use the manual configuration in docs/clients.md")
    if "codex" in clients:
        backup(codex_home/"config.toml")
        subprocess.run(["codex", "mcp", "add", "gpu_lab", "--", args.command, "mcp"], check=True)
    if "claude" in clients:
        register_claude(home, args.command)
    if args.with_instructions:
        block = (Path(__file__).resolve().parent/"docs/agent-instructions.md").read_text()
        for client in clients:
            path = codex_home/"AGENTS.md" if client == "codex" else home/".claude/CLAUDE.md"
            update_instructions(path, block)
    print("Registered. Reconnect GPU Lab in the selected clients to load its tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
