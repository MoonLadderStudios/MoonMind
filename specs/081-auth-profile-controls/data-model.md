# Data Model: Auth-Profile and Rate-Limit Controls (081)

**Created**: 2026-03-15

## Entities

### ManagedAgentAuthProfile (Pydantic contract — already exists)

Location: `moonmind/schemas/agent_runtime_models.py`

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `str` | Unique profile identifier (never the credential itself) |
| `runtime_id` | `str` | Runtime family (`gemini_cli`, `claude_code`, `codex_cli`) |
| `auth_mode` | `Literal["oauth", "api_key"]` | Auth mechanism for env shaping |
| `volume_ref` | `str | None` | Reference to OAuth credential volume (OAuth mode) |
| `account_label` | `str | None` | Human-readable account label |
| `max_parallel_runs` | `int (≥1)` | Per-profile concurrency ceiling |
| `cooldown_after_429` | `int | None` | Seconds to sidelined profile after 429 |
| `rate_limit_policy` | `dict` | Provider-specific rate limit configuration |
| `enabled` | `bool` | Whether profile is available for assignment |

### EnvironmentSpec (new — output of auth profile resolution)

Returned by `ManagedAgentAdapter` before runtime launch:

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `str` | Resolved profile ID for audit trail |
| `runtime_id` | `str` | Runtime family |
| `env_vars` | `dict[str, str]` | Env vars to inject (never contains raw credentials) |
| `cleared_vars` | `list[str]` | Env var names to explicitly unset |
| `volume_mount_path` | `str | None` | Path to mount auth volume (OAuth mode) |

### ProfileSlotState (in-workflow state — already exists in AuthProfileManager)

Location: `moonmind/workflows/temporal/workflows/auth_profile_manager.py`

Tracks per-profile runtime concurrency and cooldown state in Temporal workflow memory.

## DB Table (existing)

`managed_agent_auth_profiles` — PostgreSQL table via `api_service/db/models.py`

## State Machine

```
Profile slot lifecycle (per run):
  [unassigned]
      │ request_slot signal
      ▼
  [queued in AuthProfileManager]
      │ slot_assigned signal returned
      ▼
  [active lease — slot count +1]
      │ run completes / errors
      ▼
  [release_slot signal]
  [slot count -1]

429 cooldown lifecycle:
  [profile active]
      │ report_cooldown signal
      ▼
  [cooldown: cooldown_until = now + cooldown_after_429]
      │ periodic clear (60s wake cycle)
      ▼
  [profile active again]
```
