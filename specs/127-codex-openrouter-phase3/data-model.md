# Data Model Changes: Phase 3 — Codex OpenRouter Generalization

**Feature**: 127-codex-openrouter-phase3

---

## 1. Summary

This feature involves **Pydantic schema alignment only**. No database schema changes are required. The DB model (`ManagedAgentProviderProfile` in `api_service/db/models.py`) already has all provider-profile fields. The change is to bring the Pydantic model (`ManagedAgentProviderProfile` in `moonmind/schemas/agent_runtime_models.py`) into alignment.

---

## 2. Pydantic Model Changes

### 2.1 `ManagedAgentProviderProfile` — Field Diff

| Field | Before | After | Notes |
|-------|--------|-------|-------|
| `profile_id` | `str`, required | `str`, required | Unchanged |
| `runtime_id` | `str`, required | `str`, required | Unchanged |
| `provider_id` | `str | None`, optional | `str | None`, optional | Unchanged |
| `provider_label` | `str | None`, optional | `str | None`, optional | Unchanged |
| `default_model` | `str | None`, optional | `str | None`, optional | Unchanged |
| `model_overrides` | `dict[str, str]`, optional | `dict[str, str]`, optional | Unchanged |
| `auth_mode` | **`str`, required** | **REMOVED** | Removed per Constitution XIII |
| `volume_ref` | `str | None`, optional | `str | None`, optional | Unchanged |
| `account_label` | `str | None`, optional | `str | None`, optional | Unchanged |
| `max_parallel_runs` | `int`, required | `int`, required | Unchanged |
| `cooldown_after_429` | `int | None`, optional | `int` (default `900`) | Field renamed to `cooldown_after_429_seconds` in schema; default moved from nullable to 900. |
| `rate_limit_policy` | `dict`, optional | `dict`, optional | Unchanged |
| `enabled` | `bool`, optional | `bool`, optional | Unchanged |
| **`credential_source`** | — | **`str`, required** | New. Allowed: `oauth_volume`, `secret_ref`, `none` |
| **`runtime_materialization_mode`** | — | **`str`, required** | New. Allowed: `oauth_home`, `api_key_env`, `env_bundle`, `config_bundle`, `composite` |
| **`tags`** | — | **`list[str]`** | New. Default: `[]` |
| **`priority`** | — | **`int`** | New. Default: `100`, min: `0` |
| **`clear_env_keys`** | — | **`list[str]`** | New. Default: `[]` |
| **`env_template`** | — | **`dict[str, Any]`** | New. Default: `{}` |
| **`file_templates`** | — | **`list[RuntimeFileTemplate]`** | New. Default: `[]`. Type already exists. |
| **`home_path_overrides`** | — | **`dict[str, str]`** | New. Default: `{}` |
| **`command_behavior`** | — | **`dict[str, Any]`** | New. Default: `{}` |
| **`secret_refs`** | — | **`dict[str, str]`** | New. Default: `{}` |
| **`volume_mount_path`** | — | **`str | None`** | New. Default: `None` |
| **`max_lease_duration_seconds`** | — | **`int`** | New. Default: `7200`, min: `60` |
| **`owner_user_id`** | — | **`str | None`** | New. Default: `None` |

### 2.2 Required Fields — Before vs After

**Before (4 required):**
- `profile_id`, `runtime_id`, `auth_mode`, `max_parallel_runs`

**After (5 required):**
- `profile_id`, `runtime_id`, `credential_source`, `runtime_materialization_mode`, `max_parallel_runs`

Note: `max_parallel_runs` stays required (ge=1). `credential_source` and `runtime_materialization_mode` become required because they are fundamental to the provider-profile contract. The API's `ProviderProfileCreate` already requires them.

### 2.3 `ManagedRuntimeProfile` — No Changes

The launch-time model already has all provider-profile fields. No changes needed.

The existing `auth_mode: str | None = Field(None, alias="authMode")` on `ManagedRuntimeProfile` remains as optional for backward compatibility with any in-flight workflow payloads. It can be removed in a future cleanup pass when no caller references it.

---

## 3. DB Model — No Changes

The SQLAlchemy model at `/mnt/d/code/MoonMind/api_service/db/models.py` already has all fields. No migration needed.

| Column | DB Type | Notes |
|--------|---------|-------|
| `credential_source` | `ENUM('oauth_volume', 'secret_ref', 'none')` | Already present |
| `runtime_materialization_mode` | `ENUM('oauth_home', 'api_key_env', 'env_bundle', 'config_bundle', 'composite')` | Already present |
| `tags` | `JSONB` | Already present |
| `priority` | `INTEGER` (default 100) | Already present |
| `clear_env_keys` | `JSONB` | Already present |
| `env_template` | `JSONB` | Already present |
| `file_templates` | `JSONB` | Already present |
| `home_path_overrides` | `JSONB` | Already present |
| `command_behavior` | `JSONB` | Already present |
| `secret_refs` | `JSONB` | Already present |
| `volume_mount_path` | `VARCHAR(512)` | Already present |
| `max_lease_duration_seconds` | `INTEGER` (default 7200) | Already present |
| `owner_user_id` | `UUID` (nullable) | Already present |

---

## 4. API Schema — No Changes

The FastAPI request/response schemas at `/mnt/d/code/MoonMind/api_service/api/routers/provider_profiles.py` already include all provider-profile fields. No changes needed.

---

## 5. Migration Notes

### 5.1 Data Migration

No DB data migration is needed. The DB already uses `credential_source` (enum column). There is no `auth_mode` column in the `managed_agent_provider_profiles` table.

### 5.2 API Payload Compatibility

The Pydantic model uses `extra="forbid"`. Any API client that submits `authMode` in a profile creation payload will receive a `422 Unprocessable Entity` error with a clear message about the unexpected field. This is the correct behavior per Constitution Principle XIII (fail-fast for unsupported values).

### 5.3 In-Flight Workflow Safety

Temporal workflows carry `ManagedRuntimeProfile` (not `ManagedAgentProviderProfile`). The `ManagedRuntimeProfile` model has `auth_mode` as optional (`str | None`), so in-flight workflows are unaffected.

---

## 6. Enum Reference

### `credential_source` Allowed Values

| Value | Meaning |
|-------|---------|
| `oauth_volume` | Credentials live in a mounted auth volume |
| `secret_ref` | Credentials resolve from Secrets System at launch |
| `none` | No provider secret is required |

### `runtime_materialization_mode` Allowed Values

| Value | Meaning |
|-------|---------|
| `oauth_home` | Mount auth volume and set runtime home variables |
| `api_key_env` | Inject environment variables with resolved secrets |
| `env_bundle` | Inject a provider-specific environment block |
| `config_bundle` | Generate provider-specific config file(s) |
| `composite` | Combine multiple techniques |
