# Research Notes: Cursor CLI Phase 4

## R1: Cursor CLI Permission System

From CursorCli.md §6, Cursor CLI uses 5 permission types:
- `Shell(cmd)` — allowed shell commands (supports globs)
- `Read(path)` — file read access (supports `**` globs)
- `Write(path)` — file write access
- `WebFetch(domain)` — HTTP access to specific domains
- `Mcp(server:tool)` — MCP tool access

Rules support deny-takes-precedence logic. Config lives at `.cursor/cli.json`.

## R2: MDC Rule Format

Cursor rules use `.mdc` extension (Markdown with frontmatter). The standard format:
```
---
description: Rule description
globs: "pattern"
alwaysApply: true
---
# Rule content
```

Rules in `.cursor/rules/` are automatically loaded by the CLI.

## R3: MCP Config (Deferred)

MCP configuration via `.cursor/mcp.json` is marked optional in Phase 4. This requires:
- A running MCP server process
- Runtime env var interpolation

This is better suited for Phase 5 integration testing where the full stack is available.

## R4: AgentExecutionRequest.approval_policy Structure

The `approval_policy` field is `dict[str, Any]` — deliberately flexible. For Cursor CLI, we define a convention:
- `level`: `full_autonomy` | `supervised` | `restricted`
- `allow_shell`: list of allowed shell commands (for `supervised` mode)
- `allow_read`: list of allowed paths (for `restricted` mode)
- `allow_write`: list of allowed write paths
- `deny`: list of explicit deny rules
