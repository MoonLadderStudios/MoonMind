# Managed Agents Authentication

## 1. Overview

MoonMind-managed agent runtimes (Gemini CLI, Claude Code, Codex CLI) require authenticated access to their respective model providers. This document describes the authentication model, the OAuth volume system, the auth-profile registry, and how auth profiles are assigned to child workflows spawned by `MoonMind.AgentRun`.

This document builds on the execution model defined in [ManagedAndExternalAgentExecutionModel](../Temporal/ManagedAndExternalAgentExecutionModel.md), specifically Sections 6 (MoonMind-Managed Agents), 7 (First-Class Auth Profiles), and 9 (Worker Fleet Topology).

---

## 2. Authentication Modes

Each managed runtime supports at least two authentication modes:

| Runtime | OAuth Mode | API Key Mode | Auth Volume Path |
|---------|-----------|-------------|-----------------|
| Gemini CLI | `oauth` (default) | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `GEMINI_HOME/.gemini/` |
| Claude Code | `oauth` | `ANTHROPIC_API_KEY` (default) | `CLAUDE_HOME/` |
| Codex CLI | `oauth` | `OPENAI_API_KEY` (default) | `CODEX_HOME/` |

### OAuth Mode

OAuth credentials are stored as persistent files inside a Docker volume. The runtime CLI reads these credentials from a well-known directory and handles token refresh internally. OAuth mode is preferred when:

- The user's account subscription tier affects rate limits (e.g. Gemini Ultra grants higher API quotas).
- API keys are not available or not desired.
- Multiple user identities need to be tracked separately.

### API Key Mode

The runtime receives a provider API key via environment variables. API key mode is simpler to set up but does not carry user-level subscription claims and typically uses project-level quotas rather than account-level quotas.

### Environment Shaping

When OAuth mode is active, the auth system must explicitly clear API-key environment variables so the CLI does not silently fall back to key-based auth. For example, Gemini OAuth mode sets `GEMINI_API_KEY=` and `GOOGLE_API_KEY=` to `None` in the subprocess environment.

---

## 3. OAuth Volumes

### Current Design

Each runtime has a dedicated Docker named volume that stores persistent auth state:

| Volume | Default Mount Path | Contents |
|--------|-------------------|----------|
| `gemini_auth_volume` | `/var/lib/gemini-auth` | `.gemini/oauth_creds.json`, `settings.json`, `google_accounts.json` |
| `codex_auth_volume` | `/home/app/.codex` | Codex CLI config and session state |
| `claude_auth_volume` | `/home/app/.claude` | Claude CLI config and session state |

Volumes are initialized by `*-auth-init` services in `docker-compose.yaml` that create the directory structure and set correct ownership (UID 1000).

### Provisioning Credentials

Auth credentials are provisioned into volumes via scripts in `tools/`:

| Script | Runtime | Modes |
|--------|---------|-------|
| `tools/auth-gemini-volume.sh` | Gemini CLI | `--sync` (copy host creds), `--login` (interactive), `--check` (verify) |
| `tools/auth-codex-volume.sh` | Codex CLI | Interactive `codex login --device-auth` |
| `tools/auth-claude-volume.sh` | Claude Code | Interactive `claude login` |

The `--sync` mode (Gemini) copies the host user's local `~/.gemini` credentials into the Docker volume, preserving the account's subscription tier claims (e.g. Gemini Ultra). The `--login` modes authenticate interactively inside a container.

---

## 4. Auth Profile Registry

### Motivation

The current system maps one auth volume to one runtime family on one worker. This is insufficient when:

- Multiple users each have their own OAuth credentials for the same runtime.
- Different runs require different account tiers, subscription plans, or rate-limit budgets.
- Concurrency must be controlled per-credential rather than per-runtime-family.
- A profile's rate-limit budget is exhausted and runs should wait for another profile or for cooldown.

### `ManagedAgentAuthProfile`

An auth profile is a named, persistent record that binds an identity to a runtime with execution policy constraints.

```
ManagedAgentAuthProfile:
  profile_id:          str          # unique identifier, e.g. "gemini_ultra_nsticco"
  runtime_id:          str          # "gemini_cli" | "claude_code" | "codex_cli"
  auth_mode:           str          # "oauth" | "api_key"
  volume_ref:          str          # Docker volume name, e.g. "gemini_auth_vol_nsticco"
  volume_mount_path:   str          # container-side mount path, e.g. "/var/lib/gemini-auth"
  account_label:       str          # human-readable, e.g. "nsticco@gmail.com (Ultra)"
  max_parallel_runs:   int          # max concurrent AgentRun workflows using this profile
  cooldown_after_429:  duration     # mandatory pause after a 429, e.g. "5m"
  rate_limit_policy:   str          # "backoff" | "queue" | "fail_fast"
  enabled:             bool         # can be disabled without deletion
```

### Volume Naming Convention

When multiple OAuth volumes exist for the same runtime, each volume is named with a distinguishing suffix:

```
gemini_auth_vol_<label>       e.g. gemini_auth_vol_nsticco
gemini_auth_vol_<label>       e.g. gemini_auth_vol_team_ci
claude_auth_vol_<label>       e.g. claude_auth_vol_nsticco
codex_auth_vol_<label>        e.g. codex_auth_vol_org_key
```

The legacy `gemini_auth_volume`, `codex_auth_volume`, and `claude_auth_volume` names remain valid as the default single-profile case.

### Provisioning Multiple Volumes

Each volume is provisioned independently using the existing auth scripts with environment overrides:

```bash
# Provision a second Gemini OAuth volume for a CI account
GEMINI_VOLUME_NAME=gemini_auth_vol_ci \
GEMINI_AUTH_HOST_DIR=/path/to/ci-account/.gemini \
  tools/auth-gemini-volume.sh --sync
```

The `--check` mode verifies which account is stored in a given volume:

```bash
GEMINI_VOLUME_NAME=gemini_auth_vol_ci tools/auth-gemini-volume.sh --check
```

---

## 5. Profile Assignment to AgentRun Workflows

### Architecture: Singleton Resource Manager per Runtime Family

Profile slot assignment uses a **Singleton Resource Manager Workflow** pattern (`MoonMind.AuthProfileManager`). Each managed agent runtime family (e.g. `gemini_cli`, `claude_code`, `codex_cli`) gets its own long-lived manager workflow instance.

**Workflow ID convention:** `auth-profile-manager:<runtime_id>` (e.g. `auth-profile-manager:gemini_cli`)

The manager is the **single source of truth** for slot leases — it tracks which profiles have available capacity and which are in cooldown. All slot coordination flows through the manager via Temporal Signals (not Updates, which are reserved for synchronous operations).

### How Profiles Flow into Execution

```
1. AgentExecutionRequest includes:
   - agent_id: "gemini_cli"
   - execution_profile_ref: "gemini_ultra_nsticco"  (optional, explicit)
   - parameters.runtime.model: "gemini-3.1-pro-preview"

2. MoonMind.AgentRun sends a request_slot Signal to the
   AuthProfileManager for its runtime family.

3. AuthProfileManager evaluates available profiles:
   - Filters by: enabled, not in cooldown, available_slots > 0
   - Selects the profile with the most free slots

4. AuthProfileManager signals back slot_assigned with the
   selected profile_id to the requesting AgentRun.

5. The resolved profile provides:
   - volume_ref  -> mounted into the managed runtime's execution environment
   - auth_mode   -> determines environment shaping (OAuth vs API key)
   - concurrency slot -> reserved for the duration of the run
```

### Signal Protocol

The manager exposes these Signals:

| Signal | Direction | Payload |
|--------|-----------|---------|
| `request_slot` | AgentRun → Manager | `{requester_workflow_id, runtime_id}` |
| `release_slot` | AgentRun → Manager | `{requester_workflow_id, profile_id}` |
| `report_cooldown` | AgentRun → Manager | `{profile_id, cooldown_seconds}` |
| `sync_profiles` | System → Manager | `{profiles: [...]}` |
| `slot_assigned` | Manager → AgentRun | `{profile_id}` |
| `shutdown` | System → Manager | (none) |

### Waiting for Available Profiles

If all eligible profiles for a runtime are at capacity or in cooldown, the manager queues the request in FIFO order. When a slot becomes available (via `release_slot` or cooldown expiry), the manager drains the queue and signals waiting AgentRun workflows.

```
AuthProfileManager event loop:
  1. Drain pending queue (assign slots from available profiles)
  2. Clear expired cooldowns
  3. Check continue-as-new threshold (2000 events)
  4. Wait for new signals or 60s periodic wake-up
```

AgentRun workflows wait durably for the `slot_assigned` signal using `workflow.wait_condition`, with a configurable timeout for fallback or failure.

### Cooldown After 429

When a managed runtime encounters `429 RESOURCE_EXHAUSTED` errors, the AgentRun signals the manager:

```
AgentRun on 429 detected:
  Signal manager: report_cooldown(profile_id, cooldown_seconds)
  Release slot
  Re-request a slot (may get a different profile)
```

The manager marks the profile as in cooldown for the specified duration. During cooldown, the profile is ineligible for new slot reservations. If other profiles for the same runtime exist and are available, the system assigns them instead. If no profiles are available, the run waits.

### Continue-As-New for History Bounds

The manager uses Temporal's continue-as-new mechanism to prevent unbounded workflow history growth. After 2000 events, the manager serializes its current state (profiles, leases, cooldowns) and restarts with that state as input. This is transparent to all connected AgentRun workflows.

### Observability

The manager exposes a `get_state` Query that returns:
- Current runtime_id
- All profile states (slots, leases, cooldowns)
- Pending request queue
- Event count

---

## 6. Volume Mounting at Runtime

### Current Architecture

Today, all three auth volumes are statically mounted on the sandbox worker in `docker-compose.yaml`. The worker process selects which auth home to pass to the CLI subprocess via environment shaping (`_resolve_gemini_command_env`, etc.).

### Target Architecture

In the target state with the `mm.activity.agent_runtime` fleet, volume mounting becomes profile-driven:

1. The `ManagedAgentAdapter` resolves the auth profile for the run.
2. The adapter constructs the runtime environment with the profile's `volume_mount_path` and `auth_mode`.
3. The `ManagedRuntimeLauncher` ensures the profile's `volume_ref` is accessible to the execution environment.

For Docker-based execution, this means the agent-runtime worker must have access to all registered auth volumes. In the initial implementation (sandbox fleet), all volumes are pre-mounted. In the target state, the launcher may dynamically select which volume path to expose to the CLI subprocess.

### Environment Construction Per Profile

Given a resolved `ManagedAgentAuthProfile`, the execution environment is constructed as:

```python
# Gemini OAuth profile example
env = {
    "GEMINI_HOME": profile.volume_mount_path,
    "GEMINI_CLI_HOME": profile.volume_mount_path,
    "GEMINI_API_KEY": None,      # clear to force OAuth
    "GOOGLE_API_KEY": None,      # clear to force OAuth
}

# Gemini API key profile example
env = {
    "GEMINI_API_KEY": secret_store.get(profile.api_key_ref),
}

# Claude OAuth profile example
env = {
    "CLAUDE_HOME": profile.volume_mount_path,
    "ANTHROPIC_API_KEY": None,   # clear to force OAuth
}
```

---

## 7. Profile Persistence

Auth profiles are stored in the MoonMind database alongside existing configuration tables.

### Schema

```sql
CREATE TABLE managed_agent_auth_profiles (
    profile_id          TEXT PRIMARY KEY,
    runtime_id          TEXT NOT NULL,          -- gemini_cli, claude_code, codex_cli
    auth_mode           TEXT NOT NULL,          -- oauth, api_key
    volume_ref          TEXT,                   -- Docker volume name (OAuth mode)
    volume_mount_path   TEXT,                   -- container-side mount path
    account_label       TEXT,                   -- human-readable label
    api_key_ref         TEXT,                   -- secret store reference (API key mode)
    max_parallel_runs   INTEGER NOT NULL DEFAULT 1,
    cooldown_after_429  INTEGER NOT NULL DEFAULT 300,  -- seconds
    rate_limit_policy   TEXT NOT NULL DEFAULT 'backoff',
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_auth_profiles_runtime ON managed_agent_auth_profiles(runtime_id);
CREATE INDEX ix_auth_profiles_enabled ON managed_agent_auth_profiles(enabled);
```

### Runtime State (In-Memory / Temporal)

Transient concurrency state is tracked in the Temporal workflow layer, not in the database:

- `current_parallel_runs`: count of active `AgentRun` workflows holding a slot on this profile
- `cooldown_until`: timestamp when cooldown expires
- `consecutive_429_count`: rolling counter reset on successful completion

This state is reconstructed from active Temporal workflows on worker restart, avoiding stale database locks.

---

## 8. Migration Path

### Phase 1: Single Default Profile (Current + Minimal Change)

- Each runtime has one implicit default profile backed by the existing named volume.
- No database table needed yet; profile behavior is derived from environment variables.
- `tools/auth-*-volume.sh` scripts provision the single default volume.
- Existing `_resolve_*_command_env` functions continue to work unchanged.

### Phase 2: Auth Profile Registry

- Add `managed_agent_auth_profiles` table.
- Add API endpoints for CRUD on profiles.
- Add `--check` / `--list` commands to auth scripts that show registered profiles.
- `MoonMind.Run` begins passing `execution_profile_ref` to `AgentRun` when available.

### Phase 3: Multi-Volume Support

- Support multiple volumes per runtime via naming convention.
- Update auth scripts to accept `GEMINI_VOLUME_NAME` (already supported), `CODEX_VOLUME_NAME`, `CLAUDE_VOLUME_NAME`.
- Profile selector in `AgentRun` picks from available profiles.

### Phase 4: Queuing and Cooldown

- Implement slot reservation and release signaling in `MoonMind.AgentRun`.
- Implement 429-triggered cooldown with automatic profile rotation.
- Add observability for profile utilization and queue depth.

### Phase 5: Dedicated Agent Runtime Fleet

- Migrate from sandbox fleet to `mm.activity.agent_runtime`.
- Agent runtime workers mount all registered auth volumes.
- Profile-driven volume selection replaces static environment variables.

---

## 9. Security Considerations

- **No credentials in workflow payloads**: Auth profiles are referenced by `profile_id`, never by token or key value.
- **No credentials in logs or artifacts**: Environment shaping clears competing credential variables. Log capture must redact token-shaped strings.
- **Volume isolation**: Each volume is owned by UID 1000 with mode 0775. Volumes should not be shared across untrusted workloads.
- **API key mode**: Keys are resolved from the secret store at execution time, not stored in the profile table. The profile stores a `api_key_ref` pointer, not the key itself.
- **OAuth token refresh**: CLI runtimes handle token refresh internally using the refresh token stored in the volume. The refresh token is the long-lived secret; access tokens are ephemeral.

---

## 10. Summary

The managed agents authentication model progresses from today's single-volume-per-runtime setup to a profile-driven system where:

1. Multiple OAuth volumes can exist per runtime, each representing a different user account or subscription tier.
2. Auth profiles are registered in the database with concurrency limits, cooldown policies, and rate-limit strategies.
3. `MoonMind.AgentRun` selects and reserves a profile slot before launching a managed runtime.
4. If no profile slot is available, the workflow queues durably until one opens.
5. Provider-triggered 429 errors cause automatic cooldown on the offending profile, with fallthrough to other available profiles.
6. The system migrates incrementally from static environment variables to dynamic profile-driven execution.
