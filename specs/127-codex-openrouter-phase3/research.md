# Research Findings: Phase 3 — Codex OpenRouter Generalization

**Feature**: 127-codex-openrouter-phase3
**Date**: 2026-04-03

---

## 1. Codebase Audit Results

### 1.1 `ManagedAgentProviderProfile` Pydantic Model — Current State

**File**: `/mnt/d/code/MoonMind/moonmind/schemas/agent_runtime_models.py` (lines ~288-327)

Current fields:
| Field | Status | Notes |
|---|---|---|
| `profile_id` | Present, required | ✓ |
| `runtime_id` | Present, required | ✓ |
| `provider_id` | Present, optional | ✓ |
| `provider_label` | Present, optional | ✓ |
| `default_model` | Present, optional | ✓ |
| `model_overrides` | Present, optional | ✓ |
| `auth_mode` | **Present, REQUIRED** | ✗ Must be removed |
| `volume_ref` | Present, optional | ✓ |
| `account_label` | Present, optional | ✓ |
| `max_parallel_runs` | Present, required | ✓ |
| `cooldown_after_429` | Present, optional | ✓ |
| `rate_limit_policy` | Present, optional | ✓ |
| `enabled` | Present, optional | ✓ |
| `credential_source` | **Missing** | ✗ Must add |
| `runtime_materialization_mode` | **Missing** | ✗ Must add |
| `tags` | **Missing** | ✗ Must add |
| `priority` | **Missing** | ✗ Must add |
| `clear_env_keys` | **Missing** | ✗ Must add |
| `env_template` | **Missing** | ✗ Must add |
| `file_templates` | **Missing** | ✗ Must add (type `RuntimeFileTemplate` already exists) |
| `home_path_overrides` | **Missing** | ✗ Must add |
| `command_behavior` | **Missing** | ✗ Must add |
| `secret_refs` | **Missing** | ✗ Must add |
| `volume_mount_path` | **Missing** | ✗ Must add |
| `max_lease_duration_seconds` | **Missing** | ✗ Must add |
| `owner_user_id` | **Missing** | ✗ Must add |

**Finding**: The model has 13 present fields (4 required) and 13 missing fields. The `auth_mode` field is the blocker — it's required and has no equivalent in the provider-profile contract.

### 1.2 `ManagedRuntimeProfile` — Already Complete

The launch-time model (`ManagedRuntimeProfile` in the same file) already carries all provider-profile fields:
- `credential_source`
- `runtime_materialization_mode`
- `command_behavior`
- `env_template`
- `file_templates` (with `RuntimeFileTemplate` typed entries)
- `home_path_overrides`
- `clear_env_keys`
- `secret_refs`
- `auth_mode` (optional, can be left `None`)

**Finding**: The gap is exclusively in `ManagedAgentProviderProfile`, not in `ManagedRuntimeProfile`. The adapter already maps all provider-profile fields from the profile dict into `ManagedRuntimeProfile` (line 326-343).

### 1.3 DB Model — Already Complete

**File**: `/mnt/d/code/MoonMind/api_service/db/models.py` (lines 1805-1895)

The SQLAlchemy model has all provider-profile fields including:
- `credential_source` (enum: `ProviderCredentialSource`)
- `runtime_materialization_mode` (enum: `RuntimeMaterializationMode`)
- `tags`, `priority`
- `clear_env_keys`, `env_template`, `file_templates`
- `home_path_overrides`, `command_behavior`
- `secret_refs`, `volume_mount_path`
- `max_lease_duration_seconds`, `owner_user_id`

**Finding**: No DB migration is needed. The DB schema is already correct.

### 1.4 API Routes — Already Complete

**File**: `/mnt/d/code/MoonMind/api_service/api/routers/provider_profiles.py`

The REST API schemas (`ProviderProfileCreate`, `ProviderProfileUpdate`, `ProviderProfileResponse`) already include all provider-profile fields. The `_row_to_dict` helper already maps all DB columns to the response payload.

**Finding**: No API changes needed. The REST API already speaks the provider-profile contract.

### 1.5 Provider-agnostic Guarantee — Already Achieved

**Grep results for `openrouter` in `moonmind/workflows/`**: **0 matches**

Files audited:
- `moonmind/workflows/adapters/materializer.py` — Zero provider-specific branching. All behavior is driven by `file_templates` data.
- `moonmind/workflows/adapters/managed_agent_adapter.py` — Zero provider-specific branching. References `auth_mode` only for metadata tracking (line 212, 338, 403), which should be migrated to `credential_source`.
- `moonmind/workflows/temporal/runtime/launcher.py` — Zero provider-specific branching. No `provider_id` references.
- `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` — Zero provider-specific branching. Only handles CLI construction.

**Finding**: DOC-REQ-001 is already satisfied at the code level. The only remaining `auth_mode` references in the adapter are metadata, not branching logic.

### 1.6 Auto-seed Profile — Already Complete

**File**: `/mnt/d/code/MoonMind/api_service/main.py` (lines 624-688)

The auto-seed creates a fully specified OpenRouter profile (`codex_openrouter_qwen36_plus`) when `OPENROUTER_API_KEY` is present at startup. The profile includes:
- All provider-profile fields
- TOML config template via `file_templates`
- `home_path_overrides` for `CODEX_HOME`
- `command_behavior` with `suppress_default_model_flag`
- Proper `clear_env_keys`
- `secret_refs` binding

**Finding**: The auto-seed profile is already a reference implementation for config-bundle Codex providers.

---

## 2. Legacy `auth_mode` Usage Map

| Location | Usage | Action |
|---|---|---|
| `agent_runtime_models.py` line 298 | `auth_mode: str = Field(..., alias="authMode", min_length=1)` — **required field** | Remove |
| `agent_runtime_models.py` line 316 | `self.auth_mode = _require_non_blank(self.auth_mode, field_name="authMode")` | Remove |
| `managed_agent_adapter.py` line 212 | `auth_mode: str = profile.get("auth_mode", default_auth)` | Replace with `credential_source` derivation |
| `managed_agent_adapter.py` line 338 | `auth_mode=auth_mode` in `ManagedRuntimeProfile` constructor | Remove (leave as default `None`) |
| `managed_agent_adapter.py` line 403 | `"auth_mode": auth_mode` in metadata dict | Replace with `"credential_source"` |
| `test_agent_runtime_models.py` | Tests use `authMode="oauth"` | Update to `credentialSource="oauth_volume"` |

---

## 3. Compatibility Analysis for In-Flight Workflows

### 3.1 Temporal Payloads

Temporal workflows carry `ManagedRuntimeProfile` (not `ManagedAgentProviderProfile`) in their payloads. The `ManagedRuntimeProfile` model already has:
- `auth_mode: str | None = Field(None, alias="authMode")` — optional, defaults to `None`
- All provider-profile fields present

**Conclusion**: In-flight workflows are unaffected. The Pydantic model change only affects API-level validation and profile dict construction, not Temporal payloads.

### 3.2 DB Rows

The DB model already uses `credential_source` (not `auth_mode`). The `auth_mode` column in the DB (seen in migration `0b8e4befb8e5_initial_clean_migration.py`) is named `auth_mode` but typed as `ProviderCredentialSource` enum — it was already migrated at the DB level.

**Conclusion**: No data migration needed at DB level.

---

## 4. Multi-Profile Support Assessment

The system already supports multiple OpenRouter profiles:
1. The DB model has no uniqueness constraint on `(runtime_id, provider_id)` — multiple profiles per provider are allowed.
2. The REST API supports CRUD for arbitrary profiles.
3. The profile resolution logic (adapter `_resolve_profile`) supports priority-based selection among multiple matching profiles.
4. The `sync_provider_profile_manager` function syncs all enabled profiles for a runtime to the Temporal manager workflow.

**Conclusion**: Adding a second OpenRouter profile (e.g., `codex_openrouter_qwen_max`) is a data-only operation. No code changes required for DOC-REQ-002.

---

## 5. Related Open Questions

### OQ-001: Should `auth_mode` be removed or kept optional?

**Decision**: Remove entirely. Constitution Principle XIII (pre-release: delete, don't deprecate) applies. The DB model already uses `credential_source`. The auto-seed uses `credential_source`. No API route produces `auth_mode`.

### OQ-002: Are there other legacy Pydantic models using `auth_mode`?

**Audit**: Searched for `auth_mode` across the codebase. Only occurrences are:
- `agent_runtime_models.py` (the target of this change)
- `managed_agent_adapter.py` (metadata tracking, to be updated)
- DB models (already migrated to `ProviderCredentialSource` enum)

No other Pydantic models use `auth_mode`.

### OQ-003: Does `ManagedRuntimeProfile.auth_mode` also need removal?

**Assessment**: `ManagedRuntimeProfile` has `auth_mode: str | None = Field(None, alias="authMode")` as optional. Since this is the launch-time model and the adapter already populates it from the profile dict, we can leave it as optional `None`. It's not a required field and doesn't block provider-profile compliance. Future cleanup can remove it entirely when no caller references it.

---

## 6. Test Coverage Gaps

| Gap | Description |
|-----|-------------|
| `ManagedAgentProviderProfile` with full fields | No test exists that instantiates the Pydantic model with all provider-profile fields |
| Provider-agnostic guarantee | No test explicitly verifies zero provider-specific branching in materializer/adapter/launcher |
| Multi-profile launch | Integration tests exist for single OpenRouter profile; two-profile scenario not covered |
| Legacy `auth_mode` rejection | No test verifies that submitting `authMode` to the aligned schema is rejected |
