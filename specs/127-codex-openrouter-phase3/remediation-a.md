# Remediation Report: 127-codex-openrouter-phase3

**Prompt**: A — Remediation Discovery
**Orchestration mode**: runtime
**Date**: 2026-04-03
**Input**: `spec.md`, `plan.md`, `tasks.md`, `speckit-analyze` output

---

## spec.md

### CRITICAL-01: Unresolved OQ-004 — Other legacy `auth_mode` surfaces not scoped
- **Location**: Open Questions table, OQ-004
- **Problem**: The spec asks "Are there any other legacy-shaped Pydantic models or API contracts that also need alignment beyond `ManagedAgentProviderProfile`?" and answers "Needs code audit," but the audit results are not incorporated into the spec's scope. The speckit-analyze audit found `OAuthProviderSpec` in `moonmind/workflows/temporal/runtime/providers/base.py` and `providers/registry.py` entries still using `auth_mode`, and these are not mentioned in any implementation or out-of-scope section.
- **Remediation**: Add a definitive answer to OQ-004: either (a) bring `OAuthProviderSpec.auth_mode` and registry entries into scope with a new user story and tasks, or (b) add an explicit "Out of Scope" note with rationale explaining why `OAuthProviderSpec.auth_mode` is semantically distinct from `ManagedAgentProviderProfile.auth_mode` and why it is safe to leave unchanged.
- **Rationale**: Per Constitution Principle XIII (delete, don't deprecate), unscoped legacy surfaces create a partial migration that contradicts the stated goal of eliminating `auth_mode`.
- **Analysis ref**: CRITICAL-01

### HIGH-01: Mission Control UI coverage for FR-010 is not addressed in any user story or task
- **Location**: Requirements — FR-010; User Story 4
- **Problem**: FR-010 states "Provider profile creation, update, and deletion MUST be available through Mission Control UI and REST API," but no user story, plan section, or task covers Mission Control UI changes. T006 only tests REST API CRUD.
- **Remediation**: Either (a) add a user story and task for Mission Control UI profile CRUD, or (b) narrow FR-010 to "REST API only" with a note deferring Mission Control UI to a follow-up feature.
- **Rationale**: An unaddressed MUST requirement means the feature cannot claim full spec compliance.
- **Analysis ref**: Completeness gap (not in speckit-analyze)

### HIGH-02: `rate_limit_policy` type divergence between DB model and Pydantic model is unaddressed
- **Location**: Requirements — FR-012; Key Entities; plan AD-002 field table
- **Problem**: The DB model stores `rate_limit_policy` as a `ManagedAgentRateLimitPolicy` enum, but the current Pydantic model has it as `dict[str, Any]`. The plan's AD-002 field-addition table does not mention this field at all, leaving the type mismatch unresolved.
- **Remediation**: Add an explicit decision to AD-002 (and the T001 field table) stating whether `rate_limit_policy` is retained as `dict[str, Any]` (current) or aligned with the DB enum, with rationale.
- **Rationale**: An unaddressed type mismatch between the Pydantic and DB models violates FR-012 ("all fields present in the DB row MUST be representable in the Pydantic schema").
- **Analysis ref**: HIGH-02

### MEDIUM-01: NFR-001 and NFR-002 performance targets have no verification task
- **Location**: Non-Functional Requirements — NFR-001, NFR-002
- **Problem**: The spec states profile resolution must complete within 500ms and materialization within 200ms, but no task in tasks.md includes a performance verification step.
- **Remediation**: Add a lightweight performance assertion to T007 (e.g., `pytest-benchmark` or a timing assertion in the integration test), or move NFR-001/NFR-002 out of scope with a note deferring to a performance audit feature.
- **Rationale**: Success criteria that claim NFR compliance without verification create a false confidence signal.
- **Analysis ref**: MEDIUM-06

---

## plan.md

### CRITICAL-01: AD-002 omits existing fields that must be retained in the model rewrite
- **Location**: Architecture Decisions — AD-002, "Add missing provider-profile fields"
- **Problem**: AD-002 lists fields to add but does not enumerate the **existing** fields that must be preserved (`profile_id`, `runtime_id`, `provider_id`, `provider_label`, `default_model`, `model_overrides`, `volume_ref`, `account_label`, `max_parallel_runs`, `cooldown_after_429`, `rate_limit_policy`, `enabled`). An implementer could accidentally drop `volume_ref` or `account_label`. The DB model has both `volume_ref` and `volume_mount_path` as separate columns; the plan adds `volume_mount_path` but never confirms `volume_ref` is kept.
- **Remediation**: Add a "Fields retained as-is" table to AD-002 listing all existing fields with their current types and defaults, confirming they are unchanged.
- **Rationale**: In a model rewrite that replaces most of the field list, omitting the retained-field inventory creates a real risk of accidental data loss.
- **Analysis ref**: CRITICAL-02

### HIGH-01: `owner_user_id` type coercion (UUID to str) not documented
- **Location**: Architecture Decisions — AD-002 field table
- **Problem**: The DB model defines `owner_user_id` as `Mapped[Optional[UUID]]`, but the plan proposes `str | None` for the Pydantic model without noting the explicit UUID-to-string serialization.
- **Remediation**: Add a note to AD-002 that `owner_user_id` undergoes explicit UUID-to-string coercion during serialization, and confirm the API router already handles this conversion.
- **Rationale**: Implicit type coercion between DB and Pydantic layers can cause silent serialization mismatches.
- **Analysis ref**: HIGH-01

### HIGH-02: No task covers metadata dict consumer audit for `auth_mode` removal
- **Location**: Component Breakdown — §3.2 (Adapter Cleanup), step 3; Risk Mitigation §5
- **Problem**: T002 step 3 replaces `"auth_mode": auth_mode` with `"credential_source": credential_source` in the metadata dict, but no grep audit verifies that downstream consumers (e.g., `artifacts.py` line 2279, `build_canonical_start_handle`, run tracking) do not read `metadata["auth_mode"]`. Removing it without updating consumers would cause `KeyError` or silent data loss.
- **Remediation**: Add a step to T002: "Grep for consumers of `metadata['auth_mode']` or `metadata.get('auth_mode')` across the codebase; update all consumers atomically to read `credential_source` instead, or temporarily derive `auth_mode` from `credential_source` for backward compatibility."
- **Rationale**: Removing a metadata key that downstream code reads is a runtime-breaking change.
- **Analysis ref**: HIGH-03

### HIGH-03: No data migration verification for existing DB rows
- **Location**: Risk Mitigation §5; Implementation Scope — "Migration path"
- **Problem**: The spec's OQ-001 recommends a one-time data migration mapping `auth_mode` values to `credential_source`. The plan confirms "DB model already uses `credential_source`" and "No data migration needed at DB level," but does not verify that existing DB rows actually have valid `credential_source` values (rows created before the DB schema added `credential_source` may have NULL or stale values).
- **Remediation**: Add a verification step to T007: "Query `managed_agent_provider_profiles` for rows where `credential_source IS NULL` or has an invalid value; confirm zero results, or document a data-fix procedure."
- **Rationale**: Even if the DB schema has the column, old rows may lack the data, and the new Pydantic model requires `credential_source` as a field.
- **Analysis ref**: HIGH-04

### MEDIUM-01: T001 constraint values not fully specified per field
- **Location**: Component Breakdown — §3.1, step 2 (field table)
- **Problem**: T001 says "Each field uses `min_length=1` on required strings and `ge=0` / `ge=1` / `ge=60` constraints where applicable" but the field table does not specify which constraint applies to which field.
- **Remediation**: Add a "constraints" column to the T001 field table in the plan (e.g., `credential_source: min_length=1`, `priority: ge=0, default=100`, `max_lease_duration_seconds: ge=60, default=7200`, `cooldown_after_429: ge=0`, `max_parallel_runs: ge=1`).
- **Rationale**: Ambiguous constraints invite implementer guesswork and may diverge from DB model defaults.
- **Analysis ref**: MEDIUM-01

---

## tasks.md

### CRITICAL-01: T006 claims no dependencies but requires T001's Pydantic changes
- **Location**: T006 — "Dependencies: None (can run in parallel with T001-T005)"
- **Problem**: T006 integration tests construct profiles with `credential_source="secret_ref"` and `runtime_materialization_mode="composite"` — fields that do not exist in the Pydantic model until T001 is complete. The Pydantic validation layer will reject these fields if T001 has not been applied, causing T006 tests to fail.
- **Remediation**: Change T006 dependencies to "T001" (minimum), or restructure T006 to test only via the raw API layer (bypassing Pydantic validation) and mark Pydantic-integration testing as a separate post-T001 task.
- **Rationale**: A task claiming parallel execution that will actually fail is a dependency ordering defect that breaks CI if tasks are parallelized.
- **Analysis ref**: CRITICAL-03

### MEDIUM-01: T005 dependency on T002 is unnecessary
- **Location**: T005 — "Dependencies: T001, T002"
- **Problem**: T005 is a grep audit for provider-specific branching (`openrouter`, `openai`, `anthropic` in materializer/adapter/launcher). This audit is about provider-agnostic guarantee, not about `auth_mode` vs `credential_source`. The grep already returns 0 matches today.
- **Remediation**: Remove T002 from T005's dependencies. T005 depends only on T001 (or has no hard dependencies at all).
- **Rationale**: Unnecessary dependencies lengthen the critical path and delay verification feedback.
- **Analysis ref**: MEDIUM-02

### MEDIUM-02: T003 and T004 modify the same test file without conflict guidance
- **Location**: Task Dependency Graph; T003 and T004 artifact paths
- **Problem**: Both T003 and T004 edit `tests/unit/schemas/test_agent_runtime_models.py`. The graph shows T003 -> T004 sequentially, but if executed by different agents or in parallel branches, there is merge-conflict risk.
- **Remediation**: Add a note to T004: "Note: T003 and T004 both edit `test_agent_runtime_models.py`. Execute atomically or merge before running T007."
- **Rationale**: Same-file edits by separate tasks are a common source of integration breakage in automated task execution.
- **Analysis ref**: MEDIUM-03

### MEDIUM-03: 6 of 10 edge cases have no verification task
- **Location**: Edge Cases table in spec; tasks.md T004 coverage
- **Problem**: The spec defines 10 edge cases (EC-001 through EC-010). T004 covers EC-002, EC-007, EC-008. EC-001 (merge strategy), EC-003 (missing secrets), EC-004 (path traversal), EC-006 (env_template ref validation), EC-009 (base env protection), and EC-010 (file permissions) have no verification task.
- **Remediation**: Add a step to T005 or T007: "Confirm that edge cases EC-001, EC-003, EC-004, EC-006, EC-009, EC-010 are covered by existing materializer/adapter unit tests. If not, document as follow-up tasks."
- **Rationale**: Unverified edge cases create a false sense of spec completeness.
- **Analysis ref**: MEDIUM-05

### MEDIUM-04: Missing test for `extra="forbid"` with unknown future fields
- **Location**: T004 test list
- **Problem**: T004 test 4 verifies that legacy `authMode` is rejected via `extra="forbid"`, but there is no test verifying that arbitrary unknown fields (not just `authMode`) are also rejected, which is the protective behavior the plan relies on (AD-004 risk section).
- **Remediation**: Add a test `test_managed_agent_provider_profile_forbids_unknown_fields` to T004 that passes an arbitrary unknown field and asserts `ValidationError`.
- **Rationale**: Testing the specific legacy field does not fully validate the `extra="forbid"` contract for future unknown fields.
- **Analysis ref**: MEDIUM-04

---

## docs

### MEDIUM-01: No `docs/tmp/` migration tracker created for this feature
- **Location**: Constitution Principle XII; spec Implementation Scope
- **Problem**: The spec's "Implementation Scope" describes a migration path for `auth_mode` removal and schema alignment. Per Constitution Principle XII, migration/implementation tracking should live under `docs/tmp/`, not in the canonical spec. No `docs/tmp/` tracker exists yet for this feature.
- **Remediation**: Create a migration tracker file at `docs/tmp/remaining-work/127-codex-openrouter-phase3.md` (or equivalent) to track in-flight implementation progress, removable when the feature ships.
- **Rationale**: Constitution Principle XII requires migration narratives to live in disposable scratch space, not canonical docs.
- **Analysis ref**: Constitution alignment gap (not in speckit-analyze)

---

## Determination

### Safe to Implement: NO

### Blocking Remediations

| Severity | ID | Summary |
|----------|----|---------|
| CRITICAL | spec CRITICAL-01 | Unresolved OQ-004: `OAuthProviderSpec` and registry entries still use `auth_mode`; must be explicitly scoped in or out. |
| CRITICAL | plan CRITICAL-01 | AD-002 omits existing fields to retain; risk of accidental data loss during model rewrite. |
| CRITICAL | tasks CRITICAL-01 | T006 claims no dependencies but requires T001's Pydantic changes; parallel execution will fail. |
| HIGH | spec HIGH-01 | FR-010 Mission Control UI requirement is unaddressed in all artifacts. |
| HIGH | spec HIGH-02 | `rate_limit_policy` type mismatch between DB and Pydantic models unaddressed. |
| HIGH | plan HIGH-02 | No metadata dict consumer audit for `auth_mode` removal; downstream `KeyError` risk. |
| HIGH | plan HIGH-03 | No data migration verification for existing DB rows with potentially NULL `credential_source`. |

### Determination Rationale

Three CRITICAL findings create direct implementation risk: unresolved legacy surface scope (`OAuthProviderSpec`), incomplete field inventory in the model rewrite (data loss risk), and an incorrect task dependency graph (parallel CI failure). Four HIGH findings add secondary risk around unaddressed MUST requirements (FR-010), type mismatches, metadata consumer breakage, and data integrity. All seven must be resolved before implementation can proceed safely.
