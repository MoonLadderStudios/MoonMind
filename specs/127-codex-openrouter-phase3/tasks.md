# Tasks: Codex OpenRouter Phase 3 — Generalization

**Feature**: 127-codex-openrouter-phase3
**Mode**: runtime
**Branch**: `127-codex-openrouter-phase3`

## DOC-REQ Traceability

| DOC-REQ | Implementation Tasks | Validation Tasks |
|---------|---------------------|------------------|
| DOC-REQ-001: OpenRouter as reference implementation | (already satisfied — code audit) | T005 |
| DOC-REQ-002: Additional OpenRouter model defaults | (data-only — no code changes) | T006 |
| DOC-REQ-003: Legacy auth-profile alignment | T001, T002, T003 | T004, T007 |

---

## Phase 1 — Foundation: Pydantic Model Rewrite

### T001 — Rewrite `ManagedAgentProviderProfile` in `agent_runtime_models.py` [M]

**Type**: Implementation
**Artifact**: `moonmind/schemas/agent_runtime_models.py`
**DOC-REQ**: DOC-REQ-003
**Plan**: §3.1
**FR**: FR-007, FR-012

1. **Remove** the `auth_mode` field:
   - Delete `auth_mode: str = Field(..., alias="authMode", min_length=1)`
   - Delete `self.auth_mode = _require_non_blank(self.auth_mode, field_name="authMode")` from `_validate_policy`

2. **Add** the following provider-profile fields to `ManagedAgentProviderProfile`:

   | Field | Type | Default | Alias |
   |-------|------|---------|-------|
   | `credential_source` | `str`, required | — | `credentialSource` |
   | `runtime_materialization_mode` | `str`, required | — | `runtimeMaterializationMode` |
   | `tags` | `list[str]` | `[]` | `tags` |
   | `priority` | `int` | `100` | `priority` |
   | `clear_env_keys` | `list[str]` | `[]` | `clearEnvKeys` |
   | `env_template` | `dict[str, Any]` | `{}` | `envTemplate` |
   | `file_templates` | `list[RuntimeFileTemplate]` | `[]` | `fileTemplates` |
   | `home_path_overrides` | `dict[str, str]` | `{}` | `homePathOverrides` |
   | `command_behavior` | `dict[str, Any]` | `{}` | `commandBehavior` |
   | `secret_refs` | `dict[str, str]` | `{}` | `secretRefs` |
   | `volume_mount_path` | `str \| None` | `None` | `volumeMountPath` |
   | `max_lease_duration_seconds` | `int` | `7200` | `maxLeaseDurationSeconds` |
   | `owner_user_id` | `str \| None` | `None` | `ownerUserId` |

   Each field uses `min_length=1` on required strings and `ge=0` / `ge=1` / `ge=60` constraints where applicable (priority ge=0, max_parallel_runs ge=1, max_lease_duration_seconds ge=60, cooldown_after_429 ge=0).

3. **Update** `_validate_policy` validator:
   - Add non-blank validation for `credential_source` and `runtime_materialization_mode`
   - Remove the `auth_mode` validation line

4. **Add** fail-fast validators for `credential_source` and `runtime_materialization_mode`:
   - `credential_source` must be one of: `oauth_volume`, `secret_ref`, `none`
   - `runtime_materialization_mode` must be one of: `oauth_home`, `api_key_env`, `env_bundle`, `config_bundle`, `composite`
   - Raise `ValueError` with a clear message for unsupported values

**Dependencies**: None

---

### T002 — Replace `auth_mode` references with `credential_source` in adapter [S]

**Type**: Implementation
**Artifact**: `moonmind/workflows/adapters/managed_agent_adapter.py`
**DOC-REQ**: DOC-REQ-003
**Plan**: §3.2
**FR**: FR-006, FR-007

1. **Line ~212**: Replace `profile.get("auth_mode", default_auth)` with `profile.get("credential_source", default_credential_source)` where `default_credential_source` derives from the strategy's `default_auth_mode` via a simple mapping:
   ```python
   _LEGACY_AUTH_TO_CREDENTIAL_SOURCE = {
       "api_key": "secret_ref",
       "oauth": "oauth_volume",
   }
   default_credential_source = _LEGACY_AUTH_TO_CREDENTIAL_SOURCE.get(default_auth, "secret_ref")
   credential_source = profile.get("credential_source", default_credential_source)
   ```

2. **Line ~338**: Remove `auth_mode=auth_mode` from the `ManagedRuntimeProfile` constructor call. The `ManagedRuntimeProfile` already has `auth_mode: str | None = Field(None)` as optional, so leaving it unset is correct.

3. **Line ~403**: Replace `"auth_mode": auth_mode` in the metadata dict with `"credential_source": credential_source`.

4. **Remove** the `_LEGACY_AUTH_TO_CREDENTIAL_SOURCE` mapping if it is only used as a local variable — or keep it at module level if the mapping is useful elsewhere.

**Dependencies**: T001 (model must have `credential_source` field before adapter references it)

---

## Phase 2 — Test Updates: Schema Alignment

### T003 — Update existing unit tests to use `credentialSource` instead of `authMode` [S]

**Type**: Implementation
**Artifact**: `tests/unit/schemas/test_agent_runtime_models.py`
**DOC-REQ**: DOC-REQ-003
**Plan**: §3.4
**FR**: FR-007

1. In `test_managed_agent_provider_profile_rejects_sensitive_policy_keys`:
   - Replace `authMode="oauth"` with `credentialSource="oauth_volume", runtimeMaterializationMode="oauth_home"`

2. In `test_managed_agent_provider_profile_accepts_valid_per_profile_limits`:
   - Replace `authMode="oauth"` with `credentialSource="oauth_volume", runtimeMaterializationMode="oauth_home"`

3. Verify no other test files reference `authMode` in `ManagedAgentProviderProfile` constructions (grep audit completed — only 2 occurrences found in this file).

**Dependencies**: T001

---

### T003b — Migrate `test_managed_agent_adapter.py` from `auth_mode` to `credential_source` [S]

**Type**: Implementation
**Artifact**: `tests/unit/workflows/adapters/test_managed_agent_adapter.py`
**DOC-REQ**: DOC-REQ-003
**Plan**: §3.4
**FR**: FR-007

Update all 16 `auth_mode` references in `test_managed_agent_adapter.py` (lines 157, 158, 185, 193, 194, 222, 230, 238, 247, 404, 628, 678, 725, 758, 793, 1497):

1. Replace fixture dict keys `"auth_mode"` with `"credential_source"` and update values using the legacy mapping (`"oauth"` → `"oauth_volume"`, `"api_key"` → `"secret_ref"`).
2. Update the assertion at line ~185 from `metadata["auth_mode"]` to `metadata["credential_source"]` with the expected migrated value.
3. Verify all 16 references are migrated and the adapter test suite passes.

**Dependencies**: T002

---

### T003c — Remove legacy `auth_mode` from outgoing API response in `artifacts.py` [S]

**Type**: Implementation
**Artifact**: `moonmind/workflows/temporal/artifacts.py`
**DOC-REQ**: DOC-REQ-003
**Plan**: §3.2 (API response projection)
**FR**: FR-007, FR-015

In `build_canonical_start_handle` (line ~2279), the profile projection dict currently emits both `auth_mode` and `credential_source`:
```python
"auth_mode": "oauth" if row.credential_source.value == "oauth_volume" else "api_key",
"credential_source": row.credential_source.value,
```

Remove the derived `auth_mode` key from this projection. `credential_source` is the canonical field; `auth_mode` is legacy and no longer emitted in outgoing API responses. Per Constitution Principle XIII (pre-release: delete, don't deprecate), no backward-compat alias is retained.

**Dependencies**: T001

---

### T004 — Add validation tests for new Pydantic fields [M]

**Type**: Validation
**Artifact**: `tests/unit/schemas/test_agent_runtime_models.py`
**DOC-REQ**: DOC-REQ-003
**Plan**: §6.1
**FR**: FR-007, FR-012
**Edge Cases**: EC-002, EC-007, EC-008

Add the following new test functions:

1. **`test_managed_agent_provider_profile_accepts_full_provider_contract`**:
   - Instantiate `ManagedAgentProviderProfile` with all provider-profile fields including `credentialSource="secret_ref"`, `runtimeMaterializationMode="composite"`, `fileTemplates` with a TOML entry, `homePathOverrides`, `commandBehavior`, `clearEnvKeys`, `secretRefs`, `tags`, `priority`, `volumeMountPath`, `maxLeaseDurationSeconds`, `ownerUserId`
   - Assert no `ValidationError`
   - Assert all fields round-trip correctly via `model_dump(by_alias=True)`

2. **`test_managed_agent_provider_profile_rejects_invalid_credential_source`**:
   - Instantiate with `credentialSource="invalid_value"`
   - Assert `ValidationError` is raised with message indicating the allowed values

3. **`test_managed_agent_provider_profile_rejects_invalid_materialization_mode`**:
   - Instantiate with `runtimeMaterializationMode="invalid_mode"`
   - Assert `ValidationError` is raised with message indicating the allowed values

4. **`test_managed_agent_provider_profile_rejects_legacy_auth_mode`**:
   - Instantiate with `authMode="oauth"` (and no `credentialSource`)
   - Assert `ValidationError` is raised due to `extra="forbid"` — the field `authMode` is unexpected

5. **`test_managed_agent_provider_profile_forbids_unknown_fields`** (MEDIUM):
   - Instantiate with an arbitrary unknown field (e.g., `someFutureField="value"`) alongside valid required fields
   - Assert `ValidationError` is raised — validates the `extra="forbid"` contract for future unknown fields, not just known legacy fields

6. **`test_managed_agent_provider_profile_accepts_credential_source_none`** (EC-007):
   - Instantiate with `credentialSource="none"` and `runtimeMaterializationMode="config_bundle"`
   - Assert no `ValidationError` — config-only provider with no credentials is valid

7. **`test_managed_runtime_profile_roundtrips_file_templates`**:
   - Construct a `ManagedRuntimeProfile` with `file_templates` containing a TOML entry
   - Call `model_dump(by_alias=True)` and re-parse via `ManagedRuntimeProfile.model_validate`
   - Assert the round-trip preserves all fields

**Dependencies**: T001

> **Note**: T003 and T004 both edit `tests/unit/schemas/test_agent_runtime_models.py`. Execute atomically or merge before running T007 to avoid merge conflicts.

---

## Phase 3 — Verification: Provider-Agnostic Guarantee

### T005 — Code audit: verify zero provider-specific branching in materializer/adapter/launcher [S]

**Type**: Validation
**Artifact**: Shell commands (grep audits)
**DOC-REQ**: DOC-REQ-001
**Plan**: §3.3 (AD-003)
**FR**: FR-011
**Success Criterion**: SC-002

Run the following grep audits and confirm zero matches in production code paths:

1. `grep -rn "openrouter" moonmind/workflows/` — expect 0 matches
2. `grep -rn "openai\|anthropic" moonmind/workflows/adapters/materializer.py moonmind/workflows/adapters/managed_agent_adapter.py moonmind/workflows/temporal/runtime/launcher.py` — expect 0 matches in provider-branching context (provider names may appear only in comments or docstrings)
3. Verify that all provider behavior is driven by `file_templates`, `env_template`, `command_behavior`, `credential_source`, and `runtime_materialization_mode` data fields

Document findings inline in this task. If any provider-specific branching is found, create a follow-up task to remove it.

**Dependencies**: T001

---

## Phase 4 — Integration Tests: Multi-Profile Support

### T006 — Integration test: multi-profile OpenRouter support [L]

**Type**: Validation
**Artifact**: New test file or extend existing
**DOC-REQ**: DOC-REQ-002
**Plan**: §6.3
**FR**: FR-001, FR-014
**Success Criterion**: SC-001, SC-004

Create or extend integration tests to verify multi-profile OpenRouter support:

1. **Test: Create second OpenRouter profile via REST API and verify distinct model selection**:
   - Create a profile with `runtime_id="codex_cli"`, `provider_id="openrouter"`, `default_model="qwen/qwen-max"`, `credential_source="secret_ref"`, `runtime_materialization_mode="composite"`, appropriate `file_templates`
   - Create a second profile with the same `runtime_id` and `provider_id` but `default_model="anthropic/claude-sonnet-4-20250514"` and different `priority`
   - Resolve each profile by exact `execution_profile_ref` and assert the correct `default_model` is returned
   - Assert both profiles are independently usable (no code changes per profile)

2. **Test: Priority-based selection among two OpenRouter profiles** (FR-009):
   - Create two profiles with `provider_id="openrouter"` and different priorities (50 and 150)
   - Submit a request with `profile_selector.provider_id = "openrouter"`
   - Assert the higher-priority profile (150) is selected
   - Disable the higher-priority profile
   - Assert the lower-priority profile (50) is selected instead

3. **Test: Profile CRUD via REST API** (FR-010):
   - POST to create a new OpenRouter profile
   - GET to retrieve and verify the profile
   - PATCH to update `default_model` or `priority`
   - GET to verify the update is reflected

**Dependencies**: T001 (Pydantic model must accept `credential_source` and `runtime_materialization_mode` before integration tests can construct valid profile payloads)

---

## Phase 5 — Full Test Suite Verification

### T007 — Run full unit and integration test suite, fix any breakage [M]

**Type**: Validation
**Artifact**: `./tools/test_unit.sh` + integration test suite
**DOC-REQ**: DOC-REQ-003
**Plan**: §5
**Success Criterion**: SC-003, SC-006

1. Run `./tools/test_unit.sh` and capture output
2. Fix any test failures caused by the `auth_mode` → `credential_source` migration
3. Run integration tests: `docker compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -q --tb=short"` (or `tools/test-integration.ps1`)
4. Verify no regressions in existing provider-profile tests
5. Verify `test_provider_profile_auto_seed.py` still passes (existing OpenRouter auto-seed test)
6. **Edge case coverage audit**: Confirm that the following edge cases from the spec are covered by existing unit or integration tests. If any are not covered, document as follow-up tasks:
   - EC-001 (file_templates merge strategy with existing path)
   - EC-003 (missing secret at launch time)
   - EC-004 (path traversal in file_templates)
   - EC-006 (env_template references undefined secret_ref)
   - EC-009 (clear_env_keys protects base environment)
   - EC-010 (generated config file permissions)
7. **DB data integrity check**: Query `managed_agent_provider_profiles` for rows where `credential_source IS NULL` or not in `(oauth_volume, secret_ref, none)`. Confirm zero results, or document a data-fix procedure.

**Dependencies**: T001, T002, T003, T004

---

## Task Dependency Graph

```
T001 (Model rewrite)
  ├── T002 (Adapter cleanup)
  │     ├── T003b (Adapter test migration)
  │     │     └── T007 (Full test suite)
  │     └── T005 (Provider-agnostic audit)
  ├── T003 (Test updates)
  │     └── T004 (New validation tests)
  │           └── T007 (Full test suite)
  ├── T003c (artifacts.py API response cleanup)
  │     └── T007 (Full test suite)
  └── T006 (Integration tests) [depends on T001]
```

## Execution Order

1. **T001** — Rewrite `ManagedAgentProviderProfile` (foundation, no deps)
2. **T002** — Replace `auth_mode` in adapter (depends on T001)
3. **T003** — Update existing tests (depends on T001)
4. **T003b** — Migrate `test_managed_agent_adapter.py` (depends on T002)
5. **T003c** — Remove `auth_mode` from `artifacts.py` API response (depends on T001)
6. **T004** — Add new validation tests (depends on T001)
7. **T005** — Provider-agnostic code audit (depends on T002)
8. **T006** — Multi-profile integration tests (depends on T001)
9. **T007** — Full test suite run (depends on T003, T003b, T003c, T004)

## Complexity Summary

| Task | Complexity | Type |
|------|-----------|------|
| T001 | M | Implementation — Pydantic model rewrite (13 new fields, 1 removal, validators) |
| T002 | S | Implementation — adapter cleanup (3 locations) |
| T003 | S | Implementation — update 2 existing test constructions |
| T003b | S | Implementation — migrate 16 `auth_mode` references in adapter test file |
| T003c | S | Implementation — remove derived `auth_mode` from API response projection |
| T004 | M | Validation — 6 new test functions |
| T005 | S | Validation — grep audit |
| T006 | L | Validation — 3 integration test scenarios |
| T007 | M | Validation — full test suite execution |

## Runtime Scope Validation

- [X] T001: Python production code — `moonmind/schemas/agent_runtime_models.py`
- [X] T002: Python production code — `moonmind/workflows/adapters/managed_agent_adapter.py`
- [X] T003: Python test code — `tests/unit/schemas/test_agent_runtime_models.py`
- [X] T003b: Python test code — `tests/unit/workflows/adapters/test_managed_agent_adapter.py`
- [X] T003c: Python production code — `moonmind/workflows/temporal/artifacts.py`
- [X] T004: Python test code — `tests/unit/schemas/test_agent_runtime_models.py` (new tests)
- [X] T005: Validation — grep audit of production code paths
- [X] T006: Python integration tests — new test file or extended existing
- [X] T007: Validation — full test suite execution
