# Agent clients

Install the service first. Each client starts a small stdio adapter; jobs live in the boot services and continue if the adapter disconnects.

## Optional registration helper

```sh
python3 register_clients.py --client both --with-instructions
python3 register_clients.py --client both --with-instructions --apply
```

The first command previews; the second updates configuration. Choose `codex`, `claude`, or `both`. `--with-instructions` also updates only the marked GPU Lab section of the selected clients' global instructions. Run as your normal account, without sudo. Private backups are made before replacement. Codex registration uses its CLI and respects `CODEX_HOME` if configured; Claude registration preserves unrelated keys in `~/.claude.json`.

If a client changes configuration concurrently, retry after it has finished. The helper checks Claude/instruction-file content before replacing it. It does not edit project-specific configurations or other users' accounts.

## Manual configuration

Codex user `config.toml`:

```toml
[mcp_servers.gpu_lab]
command = "/usr/local/bin/gpu-lab"
args = ["mcp"]
```

Claude Code user configuration:

```json
{
  "mcpServers": {
    "gpu_lab": {
      "type": "stdio",
      "command": "/usr/local/bin/gpu-lab",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

Merge this entry into existing configuration. Other stdio MCP clients can use the same command and arguments. No GPU Lab password, network endpoint or sudo is required.

Reconnect the MCP server after installation or an upgrade. A long-lived adapter retains the Python code it originally loaded. `gpu_status` reporting `mode: "shared-queue"` and `scheduler_version: 4` confirms the current route. The CLI launches fresh code immediately and can be used while a client reconnects.

The reusable [agent instruction block](agent-instructions.md) explains automatic GPU sharing, workload hints, safe graphical tests and job-scoped input.
