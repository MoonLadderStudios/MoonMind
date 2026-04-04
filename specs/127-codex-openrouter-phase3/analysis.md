# speckit-analyze Report: 127-codex-openrouter-phase3

**Date**: 2026-04-03
**Artifacts reviewed**: `spec.md`, `plan.md`, `tasks.md`
**Verdict**: Proceed with modifications required (3 CRITICAL, 4 HIGH findings)

---

## 1. Consistency

### CRITICAL-01: Unresolved OQ-004 — Other legacy `auth_mode` surfaces exist but are out of scope

The spec's open question OQ-004 asks whether other legacy Pydantic models use `auth_mode` and answers "Needs code audit." The code audit reveals **two additional legacy surfaces** that are not addressed in any artifact:

1. **`OAuthProviderSpec` TypedDict** in `/mnt/d/code/MoonMind/moonmind/workflows/temporal/runtime/providers/base.py` has `auth_mode: str` as a required field.
2. **`providers/registry.py`** has 3 entries (`gemini_cli`, `codex_cli`, `claude_code`) all using `auth_mode="oauth"`.

These are not the same model as `ManagedAgentProviderProfile`, but they represent the same legacy `auth_mode` concept in a code path adjacent to the provider-profile system. The plan's "Out-of-Scope Confirmation" does not mention these. If the goal is to eliminate `auth_mode` as a concept, these must be addressed. If they are genuinely separate subsystems (OAuth session management vs. provider profiles), this should be explicitly documented with a rationale.

**Recommendation**: If these are separate subsystems, add a note to the plan's out-of-scope section explaining why `OAuthProviderSpec.auth_mode` is a different concept from `ManagedAgentProviderProfile.auth_mode`. If they should be aligned, create a follow-up task.

### CRITICAL-02: Plan field list for AD-002 omits fields that already exist in the Pydantic model

AD-002 lists fields to "add" but does not enumerate the **existing** fields that must be preserved. The current `ManagedAgentProviderProfile` model already has: `profile_id`, `runtime_id`, `provider_id`, `provider_label`, `default_model`, `model_overrides`, `volume_ref`, `account_label`, `max_parallel_runs`, `cooldown_after_429`, `rate_limit_policy`, `enabled`. The plan should explicitly state which of these are kept unchanged. Without this, an implementer might accidentally drop `volume_ref`, `account_label`, or `rate_limit_policy`.

In particular, the DB model has **both** `volume_ref` and `volume_mount_path` as separate columns. The plan's AD-002 adds `volume_mount_path` but doesn't mention keeping `volume_ref` (which already exists in the Pydantic model). This ambiguity could lead to accidental data loss.

**Recommendation**: Add a "fields retained as-is" section to AD-002 or a before/after table for the complete model.

### HIGH-01: `owner_user_id` type mismatch between DB and Pydantic

The DB model defines `owner_user_id` as `Mapped[Optional[UUID]]` (a `uuid.UUID` type), but the plan proposes `str | None` for the Pydantic model. The API router's response schema (line 152 of `provider_profiles.py`) uses `credential_source: str` (not an enum), so the Pydantic model should convert UUID to string. This is correct behavior, but the plan should note the type coercion explicitly.

**Recommendation**: Add a note in AD-002 that `owner_user_id` is `str | None` with explicit UUID-to-string serialization.

### HIGH-02: `rate_limit_policy` type divergence between DB and Pydantic

The DB model stores `rate_limit_policy` as `ManagedAgentRateLimitPolicy` (an enum), but the current Pydantic model has it as `dict[str, Any]`. The plan does not mention this field at all -- it's implicitly retained but the type mismatch is unresolved. If the API already returns a `dict` for this field, the Pydantic model is correct and the DB enum is the mismatch. This should be clarified.

**Recommendation**: Explicitly state whether `rate_limit_policy` is retained as `dict[str, Any]` (current) or whether it should be aligned with the DB enum.

---

## 2. Completeness

### HIGH-03: No task covers migration of `auth_mode` in metadata dict at adapter line 403

T002 step 3 says to replace `"auth_mode": auth_mode` with `"credential_source": credential_source` in the metadata dict. But this metadata dict is used for run tracking and observability. The plan's risk section (§5) says "This is internal metadata, not a public API." However, `build_canonical_start_handle` in `agent_runtime_models.py` and other consumers may read this metadata. A grep for `"auth_mode"` in the codebase shows it appears in artifacts (line 2279 of `artifacts.py`). If any downstream consumer reads `metadata["auth_mode"]`, removing it without updating consumers would cause `KeyError` or silent data loss.

**Recommendation**: Add a grep audit in T002 for consumers of `metadata["auth_mode"]` and update them atomically, or leave `auth_mode` in the metadata dict temporarily (deriving it from `credential_source`) to maintain backward compatibility.

### CRITICAL-03: T006 integration tests have a hidden dependency on T001

T006 claims "Dependencies: None (can run in parallel with T001-T005)." However, the integration tests described in T006 construct profiles with `credential_source="secret_ref"` and `runtime_materialization_mode="composite"` -- fields that do not exist in the Pydantic model until T001 is complete. While the REST API layer may accept these fields (since the DB model and API router already support them), the Pydantic validation layer will reject them if T001 has not been applied. This means T006 tests **will fail** if run before T001.

**Recommendation**: Mark T006 as dependent on T001, or restructure T006 to test only via the API layer (bypassing Pydantic validation) until T001 lands.

### HIGH-04: No explicit data migration task for any existing DB rows using legacy patterns

The spec's OQ-001 recommends removing `auth_mode` entirely with a one-time data migration. The plan's AD-001 confirms this approach. However, no task covers the data migration step. The DB model already uses `credential_source` (confirmed), so no DB schema migration is needed. But if any existing profile records in the DB were created with the old Pydantic model that only had `auth_mode`, there's no task to verify their `credential_source` values are set correctly.

**Recommendation**: Add a verification step (not a full migration task) to T007 that checks existing DB rows have valid `credential_source` values. Or explicitly document why this is unnecessary (e.g., "all existing profiles were created via the API which already uses `credential_source`").

---

## 3. Ambiguity

### HIGH-05: T001 field defaults are not fully specified for constraint values

T001 says "Each field uses `min_length=1` on required strings and `ge=0` / `ge=1` / `ge=60` constraints where applicable" but the field table does not show which fields get which constraints. For example:
- `credential_source` and `runtime_materialization_mode` are required -- do they get `min_length=1`? (Yes, per the validator.)
- `max_lease_duration_seconds` -- the plan says `ge=60` but doesn't specify the default value derivation (7200 from DB model).
- `priority` -- the plan says `ge=0` but doesn't confirm the default (100 from DB model).

The implementer has enough info from the DB model defaults, but the task should be explicit.

**Recommendation**: Add a "constraints" column to the T001 field table, or reference the DB model defaults explicitly.

### MEDIUM-01: T004 test 4 (`test_managed_agent_provider_profile_rejects_legacy_auth_mode`) assumes `extra="forbid"` behavior

The test expects that passing `authMode="oauth"` will raise a `ValidationError` due to `extra="forbid"`. However, the model config is `ConfigDict(populate_by_name=True, extra="forbid")` -- this is confirmed by reading the current model. The test is correct. But if any API route or seed script sends `authMode` as a pass-through field (for backward compatibility), this will break. The plan correctly accepts this risk. No action needed, but worth noting.

---

## 4. Dependency Ordering

### MEDIUM-02: T005 could run immediately (no real dependency on T002)

T005 depends on T002 per the task graph, but the provider-agnostic audit (grep for `openrouter`, `openai`, `anthropic` in materializer/adapter/launcher) is about **provider-specific branching**, not about `auth_mode` vs `credential_source`. The grep for "openrouter" in `moonmind/workflows/` already returns 0 matches. T005 could be executed as a verification task at any point. The dependency on T002 is unnecessary.

**Recommendation**: Remove T005's dependency on T002. It only needs T001 if the adapter cleanup introduces new provider names (it won't), but as written it has no hard dependency.

### MEDIUM-03: T003 and T004 can run in parallel

T003 and T004 both edit the same test file (`test_agent_runtime_models.py`). The task graph shows T003 -> T004 (sequential), but they could conflict if both modify the same file. Given that T003 replaces `authMode` in existing tests and T004 adds new test functions, there's a low but nonzero merge-conflict risk.

**Recommendation**: Keep the sequential order but note that T003 and T004 touch the same file and should be done atomically to avoid conflicts.

---

## 5. Testing Coverage

### MEDIUM-04: Missing boundary test for `extra="forbid"` with future provider-profile fields

The test suite covers rejecting `authMode` via `extra="forbid"`, but does not cover what happens when a **new** provider-profile field (not yet in the model) is added to the DB but not the Pydantic model. With `extra="forbid"`, the API will reject any future field additions until the Pydantic model is updated. This is intentional per AD-004's risk section, but there's no test verifying this protective behavior.

**Recommendation**: Add a test `test_managed_agent_provider_profile_forbids_unknown_fields` that passes an arbitrary unknown field and asserts `ValidationError`.

### MEDIUM-05: Edge cases EC-001, EC-003, EC-004, EC-006, EC-009, EC-010 are not covered by any task

The spec defines 10 edge cases. T004 covers EC-002, EC-007, EC-008. The remaining edge cases (path traversal, missing secrets, env template ref validation, base env protection, file permissions, merge strategy) are not covered by any task in this feature. These are likely already tested in the materializer/adapter unit tests, but the tasks do not verify this.

**Recommendation**: Add a note in T005 or T007 confirming that edge cases EC-001, EC-003, EC-004, EC-006, EC-009, EC-010 are covered by existing materializer/adapter tests. If not, create follow-up tasks.

### MEDIUM-06: NFR performance targets (NFR-001, NFR-002) have no verification task

The spec states profile resolution must complete within 500ms and materialization within 200ms. No task covers performance verification. For a narrow schema-alignment change, this is likely acceptable (the change is unlikely to affect performance), but the success criteria should not claim NFR compliance without verification.

**Recommendation**: Either add a lightweight performance check to T007, or remove NFR-001/NFR-002 from this feature's scope and defer to a performance audit feature.

---

## 6. Constitution Alignment

| Principle | Assessment |
|-----------|------------|
| I. Orchestrate, Don't Recreate | PASS -- No new runtime created. |
| II. One-Click Deployment | PASS -- Auto-seed already functional. |
| III. Avoid Vendor Lock-In | PASS -- Reinforces data-driven provider agnosticism. |
| IV. Own Your Data | PASS -- No changes to data ownership. |
| V. Skills Are First-Class | N/A |
| VI. Bittersweet Lesson | PASS -- Thick contracts maintained. |
| VII. Powerful Runtime Configurability | PASS -- Profiles remain runtime-configurable. |
| VIII. Modular and Extensible Architecture | PASS -- Pydantic model matches extensible DB contract. |
| IX. Resilient by Default | PASS -- Invalid values fail fast. |
| X. Facilitate Continuous Improvement | N/A |
| XI. Spec-Driven Development | PASS -- Traceable to DOC-REQ IDs. |
| XII. Canonical Documentation | PASS -- Migration tracking belongs in `docs/tmp/`. |
| XIII. Pre-Release: Delete, Don't Deprecate | PASS -- `auth_mode` removed entirely. |

**Note**: The unresolved OQ-004 (other `auth_mode` surfaces in `OAuthProviderSpec`) could be viewed as a partial violation of Principle XIII if those surfaces are considered the same legacy pattern. This depends on whether `OAuthProviderSpec.auth_mode` is semantically equivalent to `ManagedAgentProviderProfile.auth_mode` or a distinct concept.

---

## 7. Implementation Readiness

### Overall: Ready with minor clarifications needed

The artifacts are well-structured and the scope is appropriately narrow. The primary code change (Pydantic model rewrite) is concrete and actionable. The main gaps are:

1. **Unaddressed legacy `auth_mode` surfaces** (`OAuthProviderSpec`) -- must be explicitly scoped in or out.
2. **Incomplete field enumeration** -- the plan should list all existing fields that are retained, not just those being added.
3. **T006 dependency correction** -- cannot run in parallel with T001 as claimed.
4. **Missing edge case coverage** -- 6 of 10 edge cases have no verification task.

### Priority fixes before implementation:

1. Resolve OQ-004: explicitly document why `OAuthProviderSpec.auth_mode` is in or out of scope. (CRITICAL)
2. Add a "fields retained as-is" table to AD-002. (HIGH)
3. Correct T006 dependency: mark as dependent on T001. (CRITICAL)
4. Add metadata consumer audit to T002. (HIGH)
5. Add edge case coverage note to T005 or T007. (MEDIUM)

---

## 8. Summary of Findings

| Severity | ID | Summary |
|----------|----|---------|
| CRITICAL | C-01 | Unresolved OQ-004: `OAuthProviderSpec` still uses `auth_mode` |
| CRITICAL | C-02 | AD-002 omits existing fields to retain (risk of accidental data loss) |
| CRITICAL | C-03 | T006 claims no deps but requires T001's Pydantic changes |
| HIGH | H-01 | `owner_user_id` type coercion (UUID to str) not documented |
| HIGH | H-02 | `rate_limit_policy` type mismatch (DB enum vs Pydantic dict) unaddressed |
| HIGH | H-03 | No task covers metadata dict consumer audit for `auth_mode` removal |
| HIGH | H-04 | No data migration verification for existing DB rows |
| MEDIUM | M-01 | T001 constraint values not fully specified per field |
| MEDIUM | M-02 | T005 dependency on T002 is unnecessary |
| MEDIUM | M-03 | T003/T004 touch same file -- note conflict risk |
| MEDIUM | M-04 | Missing test for `extra="forbid"` with unknown future fields |
| MEDIUM | M-05 | 6 of 10 edge cases have no verification task |
| MEDIUM | M-06 | NFR-001/NFR-002 performance targets have no verification |
