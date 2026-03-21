# Cursor CLI as a Managed Agent Runtime

Status: **Proposed**
Owners: **MoonMind Engineering**
Last Updated: **2026-03-20**

---

## 1. Overview

This document describes how [Cursor CLI](https://cursor.com/docs/cli/overview) can be added to MoonMind's managed agent system as a fourth runtime option alongside Gemini CLI, Claude Code, and Codex CLI. Cursor CLI provides a terminal-native AI agent experience with interactive and headless modes, structured output, sandbox controls, and MCP support — making it a strong candidate for managed runtime integration.

### Why Cursor CLI?

- **Headless / print mode** (`--print`, `--force`) enables fully non-interactive, scriptable execution — the primary requirement for managed agent runtimes.
- **Structured output** (`--output-format json|stream-json`) provides machine-parsable results directly, eliminating the need for output scraping.
- **Permission system** (declarative allow/deny rules for shell, read, write, web fetch, and MCP tools) maps naturally to MoonMind's sandbox and approval policies.
- **MCP support** allows tools and context servers to be shared between Cursor editor and CLI sessions.
- **Session management** (`agent ls`, `agent resume`, `--continue`) enables long-running, resumable workflows.
- **API key authentication** (`CURSOR_API_KEY`) supports CI/headless environments without browser-based OAuth.

### Relevant Cursor CLI Documentation

| Page | URL | Key Content |
|------|-----|-------------|
| Overview | [cursor.com/docs/cli/overview](https://cursor.com/docs/cli/overview) | Modes, sessions, sandbox, cloud handoff |
| Installation | [cursor.com/docs/cli/installation](https://cursor.com/docs/cli/installation) | Install methods, PATH setup, auto-update |
| Headless | [cursor.com/docs/cli/headless](https://cursor.com/docs/cli/headless) | Print mode, `--force`/`--yolo`, stream-json, media |
| Using | [cursor.com/docs/cli/using](https://cursor.com/docs/cli/using) | Plan/Ask/Agent modes, rules, shortcuts, @ context |
| Authentication | [cursor.com/docs/cli/reference/authentication](https://cursor.com/docs/cli/reference/authentication) | `agent login`, `CURSOR_API_KEY`, `agent status` |
| Configuration | [cursor.com/docs/cli/reference/configuration](https://cursor.com/docs/cli/reference/configuration) | `~/.cursor/cli-config.json`, project-level config, proxy |
| Parameters | [cursor.com/docs/cli/reference/parameters](https://cursor.com/docs/cli/reference/parameters) | CLI flags, commands reference |
| Permissions | [cursor.com/docs/cli/reference/permissions](https://cursor.com/docs/cli/reference/permissions) | Permission types, allow/deny rules, glob patterns |
| Output Format | [cursor.com/docs/cli/reference/output-format](https://cursor.com/docs/cli/reference/output-format) | text, json, stream-json (NDJSON), event types |
| GitHub Actions | [cursor.com/docs/cli/github-actions](https://cursor.com/docs/cli/github-actions) | CI integration, `CURSOR_API_KEY`, autonomy modes |
| MCP | [cursor.com/docs/cli/mcp](https://cursor.com/docs/cli/mcp) | MCP config sharing, `agent mcp` commands |
| Shell Mode | [cursor.com/docs/cli/shell-mode](https://cursor.com/docs/cli/shell-mode) | Shell exec, 30s timeout, truncation, permissions |

---

## 2. Installation and Binary Management

### Installation Methods

Cursor CLI installs via a single shell command. The binary is called `agent`.

```bash
# macOS, Linux, WSL
curl https://cursor.com/install -fsS | bash

# Windows PowerShell
irm 'https://cursor.com/install?win32=true' | iex
```

The installer places the binary in `~/.local/bin/agent` and optionally adds it to the user's PATH.

### Docker Image Strategy

For MoonMind's managed runtime, Cursor CLI should be installed in the worker Docker image at build time:

```dockerfile
# Install Cursor CLI at image build time
RUN curl https://cursor.com/install -fsS | bash \
    && ln -s /root/.local/bin/agent /usr/local/bin/agent
```

Alternatively, a lazy-install approach can be used during the first run, similar to how the existing runtimes handle updates:

```bash
# Check and install/update at activity startup
agent update  # Auto-updates to latest version
```

### Auto-Update Consideration

Cursor CLI auto-updates by default. For managed runtimes, this may need to be disabled to ensure deterministic builds. If Cursor provides a flag to disable auto-updates, it should be used. Otherwise, the Docker image pin strategy (install at build time, no runtime updates) provides version stability.

---

## 3. Authentication Model

### Authentication Modes

Cursor CLI supports two authentication modes:

| Mode | Mechanism | Environment Variable | Use Case |
|------|-----------|---------------------|----------|
| Browser OAuth | `agent login` | N/A (credentials stored locally) | Interactive setup |
| API Key | `CURSOR_API_KEY` env var or `--api-key` flag | `CURSOR_API_KEY` | CI/CD, headless, managed runtimes |

### Mapping to MoonMind Auth Profiles

API key mode is the primary mode for managed runtimes. The mapping extends the existing `ManagedAgentAuthProfile` schema:

| Field | Cursor CLI Value |
|-------|-----------------|
| `runtime_id` | `cursor_cli` |
| `auth_mode` | `api_key` (primary) or `oauth` |
| `volume_ref` | `cursor_auth_volume` (OAuth mode only) |
| `volume_mount_path` | `/home/app/.cursor` (OAuth mode only) |
| `api_key_ref` | `vault://kv/moonmind/runtimes/cursor_cli#api_key` |

### OAuth Volume (Optional)

If OAuth mode is used, credentials are stored in `~/.cursor/`. A Docker volume mirrors the existing pattern:

| Volume | Default Mount Path | Contents |
|--------|-------------------|----------|
| `cursor_auth_volume` | `/home/app/.cursor` | `cli-config.json`, session state, OAuth tokens |

### Environment Shaping

When using API key mode, the environment is straightforward:

```python
# Cursor CLI API key profile
env = {
    "CURSOR_API_KEY": secret_store.get(profile.api_key_ref),
}
```

When using OAuth mode, API key variables should be cleared to prevent fallback:

```python
# Cursor CLI OAuth profile
env = {
    "CURSOR_HOME": profile.volume_mount_path,  # if supported
    "CURSOR_API_KEY": "",  # clear to force OAuth path
}
```

### Auth Script

A new provisioning script follows the existing pattern:

| Script | Runtime | Modes |
|--------|---------|-------|
| `tools/auth-cursor-volume.sh` | Cursor CLI | `--api-key` (set key), `--login` (interactive), `--check` (verify via `agent status`) |

Verification uses Cursor's built-in status command:

```bash
agent status  # Returns current auth state
```

---

## 4. Execution Architecture

### Headless Mode (Primary Execution Path)

Cursor CLI's headless/print mode is the primary execution path for managed runtimes. This mode runs non-interactively, produces structured output, and exits when complete.

```bash
# Basic headless execution
agent -p "implement the assigned task" \
  --model "claude-4-sonnet" \
  --output-format stream-json \
  --force

# With sandbox disabled for full autonomy
agent -p "implement the assigned task" \
  --model "claude-4-sonnet" \
  --output-format stream-json \
  --force \
  --sandbox disabled
```

Key flags for managed runtime execution:

| Flag | Purpose | Recommended Value |
|------|---------|-------------------|
| `-p` / `--print` | Non-interactive mode | Always set |
| `--force` / `--yolo` | Apply file changes without confirmation | Set for autonomous execution |
| `--output-format` | Output structure | `stream-json` for real-time, `json` for final result |
| `--model` | Model selection | Per-request from `AgentExecutionRequest.parameters.model` |
| `--mode` | Agent/Plan/Ask | Default `agent`; `plan` for planning-only steps |
| `--sandbox` | Sandbox mode | `enabled` or `disabled` based on approval policy |
| `--trust` | Trust project-level config | Consider for known repositories |

### Output Parsing

The `stream-json` output format produces NDJSON (newline-delimited JSON) events:

```json
{"type": "system", "timestamp": "...", "data": {"version": "...", "model": "..."}}
{"type": "user", "timestamp": "...", "data": {"text": "..."}}
{"type": "assistant", "timestamp": "...", "data": {"text": "partial response..."}}
{"type": "tool_call", "timestamp": "...", "data": {"tool": "edit_file", "status": "started", ...}}
{"type": "tool_call", "timestamp": "...", "data": {"tool": "edit_file", "status": "completed", ...}}
{"type": "result", "timestamp": "...", "data": {"success": true, "text": "final response"}}
```

The `ManagedRunSupervisor` should parse these events to:
- Stream assistant responses to artifact-backed log storage
- Track tool calls for observability
- Detect completion via `result` event
- Detect errors and classify failure types

### Process Lifecycle

The managed runtime lifecycle for Cursor CLI follows the standard `ManagedAgentAdapter` pattern:

```
1. ManagedAgentAdapter.start(request)
   └─ Resolve auth profile (API key from Vault or OAuth volume)
   └─ Construct CLI command with flags from AgentExecutionRequest
   └─ ManagedRuntimeLauncher starts `agent -p ...` subprocess
   └─ ManagedRunSupervisor monitors stdout (NDJSON stream)

2. ManagedRunSupervisor tracks execution
   └─ Parse stream-json events in real-time
   └─ Write logs to artifact-backed storage
   └─ Detect completion/failure from exit code + result event
   └─ Signal MoonMind.AgentRun workflow

3. ManagedAgentAdapter.fetch_result(run_id)
   └─ Read final result from ManagedRunStore
   └─ Collect output artifacts from workspace

4. ManagedAgentAdapter.cancel(run_id)
   └─ Send SIGTERM to agent process
   └─ Fallback to SIGKILL after timeout
```

---

## 5. Adapter Integration

### Runtime ID

The new runtime is registered with `runtime_id = "cursor_cli"`.

### Adapter Code Changes

#### `moonmind/agents/base/adapter.py`

Add Cursor CLI to the volume mount resolver:

```python
def resolve_volume_mount_env(
    base_env: Dict[str, str],
    runtime_id: str,
    volume_mount_path: Optional[str],
) -> Dict[str, str]:
    if not volume_mount_path:
        return base_env
    shaped_env = dict(base_env)

    if runtime_id == "gemini_cli":
        shaped_env["GEMINI_HOME"] = volume_mount_path
        shaped_env["GEMINI_CLI_HOME"] = volume_mount_path
    elif runtime_id == "claude_code":
        shaped_env["CLAUDE_HOME"] = volume_mount_path
    elif runtime_id == "codex_cli":
        shaped_env["CODEX_HOME"] = volume_mount_path
    elif runtime_id == "cursor_cli":
        # Cursor uses ~/.cursor; set home if OAuth mode
        shaped_env["CURSOR_CONFIG_DIR"] = volume_mount_path

    return shaped_env
```

#### Environment Shaping

Add `CURSOR_API_KEY` to the OAuth scrubbable keys list:

```python
oauth_scrubbable_keys = [
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "CURSOR_API_KEY",  # NEW
]
```

### Command Construction

The `ManagedRuntimeLauncher` builds the CLI command from `AgentExecutionRequest`:

```python
def _build_cursor_command(request: AgentExecutionRequest) -> list[str]:
    cmd = [
        "agent",
        "-p", request.instruction,     # print/headless mode with prompt
        "--output-format", "stream-json",
        "--force",                      # auto-apply changes
    ]

    if request.parameters.model:
        cmd.extend(["--model", request.parameters.model])

    if request.parameters.get("mode"):
        cmd.extend(["--mode", request.parameters["mode"]])

    if request.parameters.get("sandbox_mode"):
        cmd.extend(["--sandbox", request.parameters["sandbox_mode"]])

    return cmd
```

---

## 6. Permission and Sandbox Mapping

### Cursor CLI Permission Types

Cursor CLI uses a declarative permission system with five types:

| Permission Type | Syntax | Example |
|----------------|--------|---------|
| `Shell` | `Shell(cmd)` | `Shell(npm test)` |
| `Read` | `Read(path)` | `Read(src/**)` |
| `Write` | `Write(path)` | `Write(src/**)` |
| `WebFetch` | `WebFetch(domain)` | `WebFetch(api.example.com)` |
| `Mcp` | `Mcp(server:tool)` | `Mcp(github:create_issue)` |

Rules support glob patterns and follow deny-takes-precedence logic.

### Mapping to MoonMind Approval Policy

MoonMind's `approval_policy` in `AgentExecutionRequest` should generate a project-level permission config at `.cursor/cli.json`:

```json
{
  "permissions": {
    "allow": [
      "Read(**)",
      "Write(**)",
      "Shell(npm test)",
      "Shell(npm run build)",
      "Shell(git *)"
    ],
    "deny": [
      "Shell(rm -rf *)",
      "WebFetch(*)"
    ]
  }
}
```

### Sandbox Mode Mapping

| MoonMind Policy | Cursor CLI Flag | Behavior |
|----------------|----------------|----------|
| `full_autonomy` | `--sandbox disabled --force` | Unrestricted execution |
| `supervised` | `--sandbox enabled` | Permissions enforced, interactive approval |
| `restricted` | `--sandbox enabled` + restrictive `.cursor/cli.json` | Minimal permissions |

---

## 7. Rules and Context Injection

### Cursor Rules System

Cursor CLI automatically loads rules from:
- `.cursor/rules` directory (project-level)
- `AGENTS.md` (repo root)
- `CLAUDE.md` (repo root, for compatibility)

### MoonMind Skill Injection

MoonMind's skill system can inject context into Cursor CLI runs by:

1. **Writing skill instructions to `.cursor/rules/`** before launching the agent
2. **Prepending skill context to the prompt** passed via `-p`
3. **Using MCP tools** for dynamic context delivery

The recommended approach is (1) — writing a `.cursor/rules/moonmind-task.mdc` file that contains the task instructions, skill constraints, and output expectations. This aligns with how Cursor CLI's rule system already works.

---

## 8. MCP Integration

Cursor CLI shares MCP configuration with the Cursor editor via `mcp.json`. MoonMind can provision MCP servers for Cursor CLI runs:

```json
// .cursor/mcp.json in the workspace
{
  "mcpServers": {
    "moonmind-context": {
      "command": "moonmind-mcp-server",
      "args": ["--run-id", "${RUN_ID}"],
      "env": {
        "MOONMIND_API_URL": "${MOONMIND_API_URL}"
      }
    }
  }
}
```

CLI management commands available:
- `agent mcp list` — list configured servers
- `agent mcp list-tools` — list available tools
- `agent mcp enable/disable` — toggle servers

---

## 9. Docker Compose Integration

### Volume Definition

```yaml
volumes:
  cursor_auth_volume:
    name: cursor_auth_volume
```

### Auth Init Service

```yaml
cursor-auth-init:
  image: alpine:3.20
  volumes:
    - cursor_auth_volume:${CURSOR_VOLUME_PATH:-/home/app/.cursor}
  command: >
    sh -c "
      mkdir -p ${CURSOR_VOLUME_PATH:-/home/app/.cursor} &&
      chown -R 1000:1000 ${CURSOR_VOLUME_PATH:-/home/app/.cursor} &&
      chmod 0775 ${CURSOR_VOLUME_PATH:-/home/app/.cursor}
    "
  restart: "no"
```

### Worker Volume Mounts

Add to the `temporal-worker-sandbox` (or future `agent_runtime` fleet):

```yaml
temporal-worker-sandbox:
  volumes:
    - cursor_auth_volume:/home/app/.cursor
    # ... existing volumes
```

---

## 10. Auth Profile Table Extension

The existing `managed_agent_auth_profiles` table supports Cursor CLI with no schema changes — only new row values:

```sql
INSERT INTO managed_agent_auth_profiles (
    profile_id, runtime_id, auth_mode,
    volume_ref, volume_mount_path,
    account_label, api_key_ref,
    max_parallel_runs, cooldown_after_429, rate_limit_policy
) VALUES (
    'cursor_default',
    'cursor_cli',
    'api_key',
    NULL,                        -- no volume needed for API key mode
    NULL,
    'Cursor CLI Default (API Key)',
    'vault://kv/moonmind/runtimes/cursor_cli#api_key',
    2,                           -- Cursor's rate limits TBD
    300,
    'backoff'
);
```

---

## 11. Output Parsing and Result Mapping

### Stream-JSON Event → MoonMind Mapping

| Cursor Event Type | MoonMind Mapping |
|-------------------|-----------------|
| `system` | Log: initialization metadata (model, version) |
| `user` | Log: echoed user prompt |
| `assistant` | Log: agent response text; stream to `LogStreamer` |
| `tool_call` (started) | Log: tool invocation; update `AgentRunStatus` metadata |
| `tool_call` (completed) | Log: tool result; track file modifications |
| `result` | `AgentRunResult.summary`; determine `success`/`failure` |

### Exit Code Mapping

| Exit Code | `FailureClass` |
|-----------|---------------|
| `0` | `None` (success) |
| `1` | `agent_error` |
| `SIGTERM/SIGKILL` | `cancelled` |
| Connection/auth errors | `provider_error` |
| Timeout | `timeout` |

---

## 12. Comparison to Existing Runtimes

| Capability | Gemini CLI | Claude Code | Codex CLI | **Cursor CLI** |
|-----------|-----------|-------------|-----------|----------------|
| Runtime ID | `gemini_cli` | `claude_code` | `codex_cli` | `cursor_cli` |
| Binary | `gemini` | `claude` | `codex` | `agent` |
| Headless mode | `--sandbox` | `--print` | `--quiet` | `--print --force` |
| Structured output | Partial | `--output-format json` | `--output-format json` | `--output-format stream-json` |
| Auth (API key) | `GEMINI_API_KEY` | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` | `CURSOR_API_KEY` |
| Auth (OAuth) | `~/.gemini/` | `~/.claude/` | `~/.codex/` | `~/.cursor/` |
| Permission system | Limited | Limited | Limited | **Full declarative** (5 types) |
| MCP support | Yes | Yes | No | **Yes** |
| Session resume | No | No | No | **Yes** (`agent resume`) |
| Sandbox controls | Basic | Basic | Basic | **Advanced** (per-command, with network) |
| Cloud Agent handoff | No | No | No | **Yes** (`-c`, `&` prefix) |
| Models available | Gemini family | Claude family | OpenAI family | **Multi-provider** |

### Key Differentiator

Cursor CLI is **multi-model**: it can use Claude, GPT, Gemini, and other models through a single CLI. This means a single `cursor_cli` runtime can access different model families without switching runtimes — a significant advantage for MoonMind's orchestration layer.

---

## 13. Implementation Plan

### Phase 1: Binary Integration

- [ ] Add Cursor CLI installation to worker Dockerfile
- [ ] Create `tools/auth-cursor-volume.sh` provisioning script
- [ ] Verify `agent status` and `agent -p` work in container environment
- [ ] Document required `CURSOR_API_KEY` in `.env.example`

### Phase 2: Adapter Wiring

- [ ] Add `cursor_cli` branch to `resolve_volume_mount_env()` in `adapter.py`
- [ ] Add `CURSOR_API_KEY` to `shape_agent_environment()` scrubbable keys
- [ ] Implement `_build_cursor_command()` in the launcher
- [ ] Add NDJSON stream parser for `stream-json` output format
- [ ] Register `cursor_cli` in `activity_catalog.py` and worker capability sets

### Phase 3: Auth Profile Support

- [ ] Seed default `cursor_cli` auth profile in database migration
- [ ] Start `AuthProfileManager` for `cursor_cli` runtime on API startup
- [ ] Add `cursor_auth_volume` to `docker-compose.yaml` (optional, for OAuth mode)

### Phase 4: Permission and Context Integration

- [ ] Implement `.cursor/cli.json` generation from MoonMind approval policies
- [ ] Implement `.cursor/rules/moonmind-task.mdc` generation for skill injection
- [ ] Wire MCP configuration for MoonMind context servers (optional)

### Phase 5: Testing and Hardening

- [ ] Unit tests for command construction and output parsing
- [ ] Integration test: end-to-end headless execution in Docker
- [ ] Verify 429/rate-limit detection and cooldown signaling
- [ ] Verify cancellation (SIGTERM → SIGKILL) path
- [ ] Dashboard visibility for `cursor_cli` runs

---

## 14. Open Questions

1. **Cursor CLI billing model**: How does Cursor's API key billing work for headless/CI usage? Are there separate rate limits or usage costs for API key mode vs. subscription-based OAuth?

2. **Auto-update control**: Does Cursor CLI provide a flag or environment variable to disable auto-updates? For managed runtimes, version pinning is important for reproducibility.

3. **Cloud Agent handoff**: Should MoonMind leverage Cursor's cloud agent (`-c` / `--cloud`) as an alternative execution path, or should all execution remain local? Cloud handoff could enable longer-running tasks without occupying worker slots, but introduces a dependency on Cursor's cloud infrastructure.

4. **Multi-model routing**: Since Cursor CLI supports multiple model providers, should MoonMind treat `cursor_cli` as a unified runtime that can route to any model, or maintain separate profiles per underlying model provider?

5. **Cursor config home path**: The exact environment variable for overriding Cursor's config directory (equivalent to `GEMINI_HOME` or `CLAUDE_HOME`) needs to be confirmed. `CURSOR_CONFIG_DIR` is a placeholder.

---

## 15. Security Considerations

- **API key handling**: `CURSOR_API_KEY` must be resolved from the secret store at execution time, never stored in workflow payloads or logs. The existing `api_key_ref` pattern in `ManagedAgentAuthProfile` applies directly.
- **Permission sandboxing**: Cursor's built-in permission system provides defense-in-depth alongside MoonMind's approval policies. The `--sandbox enabled` flag should be the default for managed runs.
- **Network access**: Cursor's `WebFetch` permission type can be used to restrict network access during execution, complementing Docker-level network isolation.
- **Log redaction**: The structured `stream-json` output should be scanned for secret-like patterns before being persisted to artifact storage, consistent with existing log redaction rules.

---

## 16. Summary

Cursor CLI is a strong candidate for MoonMind's fourth managed agent runtime. Its headless mode, structured NDJSON output, declarative permission system, and multi-model support align well with MoonMind's managed runtime architecture. Integration follows the existing adapter pattern with minimal changes to core orchestration — consistent with Constitution Principle I (Orchestrate, Don't Recreate) and Principle VIII (Modular and Extensible Architecture).

The primary integration path uses `CURSOR_API_KEY` authentication and `--print --force --output-format stream-json` for non-interactive execution. The NDJSON stream provides richer real-time observability than most existing runtime integrations, and the permission system offers fine-grained sandbox control that maps naturally to MoonMind's approval policies.
