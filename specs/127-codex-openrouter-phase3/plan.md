# Technical Implementation Plan: Phase 3 — Codex OpenRouter Generalization

**Feature**: 127-codex-openrouter-phase3
**Created**: 2026-04-03
**Status**: Draft

---

## 1. Executive Summary

Phase 3 closes the remaining gaps between the Pydantic validation layer (`ManagedAgentProviderProfile`) and the full provider-profile contract already implemented in the DB model and API. The DB model, API routes, materializer, adapter, and launcher are all provider-agnostic. The only remaining legacy surface is the Pydantic model, which still requires `auth_mode` and lacks provider-profile fields.

This plan has **one primary code change**: rewriting `ManagedAgentProviderProfile` in `moonmind/schemas/agent_runtime_models.py` to match the provider-profile contract, plus a secondary cleanup pass in the adapter to stop referencing `auth_mode`.

---

## 2. Architecture Decisions

### AD-001: Remove `auth_mode` entirely from `ManagedAgentProviderProfile` (not deprecate)

**Decision**: `auth_mode` will be removed from the Pydantic model as a field. Per Constitution Principle XIII (pre-release: delete, don't deprecate), no compatibility alias is introduced.

**Rationale**:
- `auth_mode` is semantically replaced by `credential_source` in the provider-profile contract.
- The DB model already uses `credential_source` (enum: `oauth_volume`, `secret_ref`, `none`).
- No API route or auto-seed uses `auth_mode` for creation.
- The adapter still reads `auth_mode` from the profile dict but only for metadata tracking — this can be migrated to derive from `credential_source`.

**Migration mapping** (one-time, applied at seed/data level, not code-level):
| legacy `auth_mode` | new `credential_source` |
|---|---|
| `oauth` | `oauth_volume` |
| `api_key` | `secret_ref` |

### AD-002: Add missing provider-profile fields to `ManagedAgentProviderProfile`

**Fields retained as-is** (existing fields that must be preserved unchanged):

| Field | Current Type | Default | Notes |
|---|---|---|---|
| `profile_id` | `str` | — | Required, primary identifier |
| `runtime_id` | `str` | — | Required |
| `provider_id` | `str` | — | Required |
| `provider_label` | `str` | — | Required |
| `default_model` | `str` | — | Required |
| `model_overrides` | `dict[str, Any]` | `{}` | Optional per-provider model config |
| `volume_ref` | `str \| None` | `None` | Volume reference for OAuth providers |
| `account_label` | `str \| None` | `None` | Account identifier label |
| `max_parallel_runs` | `int` | `1` | Concurrency limit |
| `cooldown_after_429` | `int` | `0` | Rate-limit backoff seconds |
| `rate_limit_policy` | `dict[str, Any]` | `{}` | Stored as dict in Pydantic; DB uses `ManagedAgentRateLimitPolicy` enum — see spec Implementation Scope §5 |
| `enabled` | `bool` | `True` | Soft-disable flag |

**Fields to add** (exist in DB model and API but absent from Pydantic model):

| Field | Type | Source |
|---|---|---|
| `credential_source` | `str` (enum: oauth_volume, secret_ref, none) | ProviderProfiles §6.1 |
| `runtime_materialization_mode` | `str` (enum: oauth_home, api_key_env, env_bundle, config_bundle, composite) | ProviderProfiles §6.1 |
| `tags` | `list[str]` | DB model + API |
| `priority` | `int` (default 100) | DB model + API |
| `clear_env_keys` | `list[str]` | ProviderProfiles §6.1 |
| `env_template` | `dict[str, Any]` | ProviderProfiles §6.1 |
| `file_templates` | `list[RuntimeFileTemplate]` | ProviderProfiles §6.1 (already has type) |
| `home_path_overrides` | `dict[str, str]` | ProviderProfiles §6.1 |
| `command_behavior` | `dict[str, Any]` | ProviderProfiles §6.1 |
| `secret_refs` | `dict[str, str]` | ProviderProfiles §6.1 |
| `volume_mount_path` | `str | None` | DB model |
| `cooldown_after_429` | `int` (already present) | Already present |
| `max_lease_duration_seconds` | `int` | DB model + API |
| `owner_user_id` | `str | None` | DB model |

> **Note on `owner_user_id` type coercion**: The DB model defines `owner_user_id` as `Mapped[Optional[UUID]]`, but the Pydantic model uses `str | None`. The API serialization layer must explicitly convert `UUID` to `str` via `str(uuid_value)` when reading from DB rows, and parse `str` back to `UUID` via `UUID(str_value)` when writing. The existing API router already handles this conversion (confirmed in `api_service/routes/provider_profiles.py`); no new coercion logic is introduced by this feature.

### AD-003: No code changes to materializer, launcher, or strategy

Code audit confirms zero provider-specific branching exists:
- `grep "openrouter" moonmind/workflows/` → **0 matches**
- Materializer is fully data-driven via `file_templates`
- Launcher has no provider-specific code paths
- `CodexCliStrategy` only handles CLI construction, not provider identity

### AD-004: Adapter `auth_mode` reference → derive from `credential_source`

The adapter (`managed_agent_adapter.py`) currently reads `auth_mode` from the profile dict at line 212. Since `credential_source` maps 1:1 with the old `auth_mode` semantics, we derive it:

```python
# Old:
auth_mode: str = profile.get("auth_mode", default_auth)

# New:
credential_source = profile.get("credential_source", default_credential_source)
# Pass credential_source into ManagedRuntimeProfile; drop auth_mode from metadata
```

---

## 3. Component Breakdown

### 3.1 `ManagedAgentProviderProfile` Pydantic Model Rewrite

**File**: `/mnt/d/code/MoonMind/moonmind/schemas/agent_runtime_models.py`

**Changes**:
1. **Remove** `auth_mode: str = Field(..., alias="authMode", min_length=1)` field
2. **Remove** `self.auth_mode = _require_non_blank(self.auth_mode, field_name="authMode")` from `_validate_policy`
3. **Add** the following fields to the model (matching DB + API schema):

```
credential_source: str = Field(..., alias="credentialSource", min_length=1)
runtime_materialization_mode: str = Field(..., alias="runtimeMaterializationMode", min_length=1)
tags: list[str] = Field(default_factory=list, alias="tags")
priority: int = Field(default=100, alias="priority", ge=0)
clear_env_keys: list[str] = Field(default_factory=list, alias="clearEnvKeys")
env_template: dict[str, Any] = Field(default_factory=dict, alias="envTemplate")
file_templates: list[RuntimeFileTemplate] = Field(default_factory=list, alias="fileTemplates")
home_path_overrides: dict[str, str] = Field(default_factory=dict, alias="homePathOverrides")
command_behavior: dict[str, Any] = Field(default_factory=dict, alias="commandBehavior")
secret_refs: dict[str, str] = Field(default_factory=dict, alias="secretRefs")
volume_mount_path: str | None = Field(None, alias="volumeMountPath")
max_lease_duration_seconds: int = Field(default=7200, alias="maxLeaseDurationSeconds", ge=60)
owner_user_id: str | None = Field(None, alias="ownerUserId")
```

4. **Update** `_validate_policy` validator to validate `credential_source` and `runtime_materialization_mode` against their allowed enum values (or let Pydantic `extra="forbid"` handle it, with explicit enum validation for clarity).

5. **Add** validators for `credential_source` and `runtime_materialization_mode` that reject unsupported values (fail-fast per Constitution Principle IX).

### 3.2 Adapter Cleanup

**File**: `/mnt/d/code/MoonMind/moonmind/workflows/adapters/managed_agent_adapter.py`

**Changes**:
1. Line 212: Replace `profile.get("auth_mode", default_auth)` with `profile.get("credential_source", default_credential_source)` where `default_credential_source` comes from the strategy's `default_auth_mode` property (renamed or mapped).
2. Line 338: Remove `auth_mode=auth_mode` from `ManagedRuntimeProfile` constructor (the launch-time model already has `auth_mode: str | None` as optional — it can be left unset or set to `None`).
3. Line 403: Remove `"auth_mode": auth_mode` from the metadata dict. Replace with `"credential_source": credential_source`.
4. **Adapter metadata dict consumer audit**: Before applying changes in steps 2–3, grep the codebase for all consumers of `metadata["auth_mode"]` or `metadata.get("auth_mode")` that read from the adapter's output metadata dict. Update all consumers atomically to read `credential_source` instead of `auth_mode`. If any consumer cannot be updated in the same PR, temporarily derive `auth_mode` from `credential_source` in the metadata dict for backward compatibility and document as a follow-up.

5. **API response projection audit (`artifacts.py`)**: Review `build_canonical_start_handle` in `artifacts.py` line ~2279, which projects DB rows directly to API response dicts (not from the adapter's metadata dict). This function currently emits both `auth_mode` (derived from `credential_source`) and `credential_source` itself. Per Constitution Principle XIII (pre-release: delete, don't deprecate), remove the derived `auth_mode` key from this projection. `credential_source` is the sole canonical field for outgoing API responses.

### 3.3 Strategy Default Auth Mapping

**File**: `/mnt/d/code/MoonMind/moonmind/workflows/temporal/runtime/strategies/base.py` (and codex_cli)

The `default_auth_mode` property on strategies is named for the legacy concept. This can stay as-is for backward compatibility since it returns a string that maps directly to `credential_source` values (`api_key` → `secret_ref`). A simple mapping layer can handle this:

```python
_LEGACY_AUTH_TO_CREDENTIAL_SOURCE = {
    "api_key": "secret_ref",
    "oauth": "oauth_volume",
}
```

However, since the adapter reads from the profile dict (which comes from the DB model), and the DB model already has `credential_source`, this mapping is only needed as a fallback default. Minimal change: just map the fallback.

### 3.4 Test Updates

**File**: `/mnt/d/code/MoonMind/tests/unit/schemas/test_agent_runtime_models.py`

Existing tests use `authMode="oauth"` in `ManagedAgentProviderProfile` constructions. These must be updated:
- Replace `authMode="oauth"` with `credentialSource="oauth_volume"`
- Replace `authMode="api_key"` with `credentialSource="secret_ref"`
- Add new tests verifying all provider-profile fields
- Add tests verifying legacy `auth_mode` is rejected (extra="forbid" handles this)

---

## 4. Implementation Sequencing

| Order | Work Item | Files Changed | Dependencies |
|-------|-----------|---------------|--------------|
| 1 | Rewrite `ManagedAgentProviderProfile` Pydantic model | `agent_runtime_models.py` | None |
| 2 | Update adapter to use `credential_source` instead of `auth_mode` | `managed_agent_adapter.py` | Step 1 |
| 3 | Update unit tests for Pydantic model changes | `test_agent_runtime_models.py` | Step 1 |
| 4 | Add provider-agnostic guarantee tests | New test file | Steps 1-2 |
| 5 | Run full test suite and fix any breakage | All | Steps 1-4 |

---

## 5. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Existing profile records use `auth_mode` only | Medium | DB model already uses `credential_source`. No data migration needed at DB level. Pydantic model is only used for validation/API payloads, not DB rows. |
| Existing DB rows have NULL or invalid `credential_source` | Medium | Add a verification step in T007: query `managed_agent_provider_profiles` for rows where `credential_source IS NULL` or not in `(oauth_volume, secret_ref, none)`. If any are found, document a data-fix procedure mapping legacy `auth_mode` values to `credential_source` equivalents. Confirm zero results before merging. |
| Adapter metadata still references `auth_mode` | Low | Update metadata dict key from `auth_mode` to `credential_source`. This is internal metadata, not a public API. |
| Tests fail due to `auth_mode` removal | Low | All existing tests are in-repo and can be updated atomically. |
| `extra="forbid"` rejects future fields | Medium | The model uses `extra="forbid"` by convention. New fields in the provider-profile contract will require explicit model updates (intentional). |
| Workflow payloads contain `auth_mode` from in-flight runs | Low | Temporal payloads carry `ManagedRuntimeProfile` (not `ManagedAgentProviderProfile`). The runtime model already has `auth_mode` as optional, so in-flight runs are unaffected. |

---

## 6. Testing Strategy

### 6.1 Unit Tests

| Test | File | Purpose |
|------|------|---------|
| `test_managed_agent_provider_profile_accepts_full_provider_contract` | `test_agent_runtime_models.py` | Instantiate with all provider-profile fields, verify no ValidationError |
| `test_managed_agent_provider_profile_rejects_invalid_credential_source` | `test_agent_runtime_models.py` | Verify invalid `credential_source` raises ValidationError |
| `test_managed_agent_provider_profile_rejects_invalid_materialization_mode` | `test_agent_runtime_models.py` | Verify invalid `runtime_materialization_mode` raises ValidationError |
| `test_managed_agent_provider_profile_rejects_legacy_auth_mode` | `test_agent_runtime_models.py` | Verify `authMode` in input is rejected by `extra="forbid"` |
| `test_managed_runtime_profile_roundtrips_file_templates` | `test_agent_runtime_models.py` | Verify `file_templates` with TOML entries round-trips through `model_dump(by_alias=True)` |

### 6.2 Boundary Tests

| Test | Purpose |
|------|---------|
| Adapter produces `ManagedRuntimeProfile` with `credential_source` | Verify no `auth_mode` leakage into runtime profile |
| Materializer zero provider-branching audit | Grep for `openrouter`, `openai`, `anthropic` in materializer/adapter/launcher — zero matches expected in code paths |

### 6.3 Integration Tests

| Test | Purpose |
|------|---------|
| Create second OpenRouter profile via REST API → launch | Verify data-only multi-profile support (FR-001, FR-014) |
| Profile resolution with two OpenRouter profiles | Verify priority-based selection works (FR-009) |

---

## 7. Constitution Check

| Principle | Assessment |
|-----------|------------|
| I. Orchestrate, Don't Recreate | **PASS** — No new runtime created. Provider-profile contract is extended. |
| II. One-Click Deployment | **PASS** — Auto-seed already creates working OpenRouter profile. Schema alignment makes it more robust. |
| III. Avoid Vendor Lock-In | **PASS** — This change removes the last Pydantic-level legacy surface and reinforces data-driven provider agnosticism. |
| IV. Own Your Data | **PASS** — No changes to data ownership model. |
| V. Skills Are First-Class | **N/A** |
| VI. Bittersweet Lesson | **PASS** — Provider-profile contract remains the thick stable interface. |
| VII. Powerful Runtime Configurability | **PASS** — Profiles remain runtime-configurable via REST API. |
| VIII. Modular and Extensible Architecture | **PASS** — This change makes the Pydantic model match the extensible DB contract. |
| IX. Resilient by Default | **PASS** — Invalid values fail fast with clear errors. |
| X. Facilitate Continuous Improvement | **N/A** |
| XI. Spec-Driven Development | **PASS** — This plan is derived from the spec with traceability to DOC-REQ-* IDs. |
| XII. Canonical Documentation | **PASS** — Implementation tracking belongs in `docs/tmp/`. |
| XIII. Pre-Release: Delete, Don't Deprecate | **PASS** — `auth_mode` is removed entirely, not aliased. |

---

## 8. DOC-REQ Traceability

| DOC-REQ-ID | Implementation | Verification |
|------------|---------------|--------------|
| DOC-REQ-001 (Reference implementation) | Grep audit confirms zero OpenRouter-specific branching in materializer, adapter, launcher. All behavior is data-driven via `file_templates`, `env_template`, `command_behavior`. | Code audit: `grep -r "openrouter" moonmind/workflows/` → 0 matches. |
| DOC-REQ-002 (Additional model defaults) | No code changes needed per new profile. Adding a new OpenRouter model profile is a data-only operation (DB row creation via API/UI). The Pydantic model alignment ensures the API layer can express the full provider-profile contract. | Manual test: create profile via REST API → launch Codex run → verify correct model. |
| DOC-REQ-003 (Legacy auth-profile alignment) | `ManagedAgentProviderProfile` Pydantic model rewritten to include all provider-profile fields and remove `auth_mode`. The dual-schema gap between DB model and Pydantic model is closed. | Unit tests: instantiate with full fields; instantiate with legacy `auth_mode`; verify rejection. |

---

## 9. Complexity Tracking

No cross-cutting changes touching many modules. The scope is confined to:
- One Pydantic model rewrite (`agent_runtime_models.py`)
- One adapter cleanup (`managed_agent_adapter.py`)
- Test file updates

All changes are within the auth/profile subsystem boundary.

---

## 10. Out-of-Scope Confirmation

The following are confirmed out of scope (matching the spec):
- New provider implementations beyond OpenRouter
- Temporal workflow orchestration changes
- Secrets System backend changes
- OpenRouter-specific optional headers (§16.3 of design doc)
- `codex_cli` strategy changes beyond existing `command_behavior` support
