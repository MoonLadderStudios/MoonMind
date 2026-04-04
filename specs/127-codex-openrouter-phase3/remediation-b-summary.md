# Remediation B Summary: 127-codex-openrouter-phase3

**Prompt**: B — Remediation Application
**Date**: 2026-04-03
**Input**: `remediation-a.md` (Remediation Discovery report)

---

## Files Changed

| File | Change Summary |
|------|---------------|
| `specs/127-codex-openrouter-phase3/spec.md` | Resolved OQ-004 (OAuthProviderSpec out of scope), narrowed FR-010 to REST API only, added rate_limit_policy type decision, added OAuthProviderSpec to out-of-scope section |
| `specs/127-codex-openrouter-phase3/plan.md` | Added "Fields retained as-is" table to AD-002, added owner_user_id UUID coercion note, added metadata consumer audit step to adapter cleanup, added DB row credential_source verification to Risk Mitigation |
| `specs/127-codex-openrouter-phase3/tasks.md` | Fixed T006 dependency (None -> T001), removed T002 from T005 dependency, added same-file conflict note for T003/T004, added unknown fields test to T004, added edge case coverage audit and DB integrity check to T007, updated dependency graph and execution order |
| `docs/tmp/remaining-work/127-codex-openrouter-phase3.md` | Created new migration tracker per Constitution Principle XII |

---

## Remediations Completed

### CRITICAL

| ID | Source | Summary | Status |
|----|--------|---------|--------|
| spec CRITICAL-01 | spec.md OQ-004 | Added definitive answer: OAuthProviderSpec.auth_mode is semantically distinct (OAuth session type, not credential sourcing). Added explicit out-of-scope note. | Done |
| plan CRITICAL-01 | plan.md AD-002 | Added "Fields retained as-is" table listing all 12 existing fields (profile_id, runtime_id, provider_id, provider_label, default_model, model_overrides, volume_ref, account_label, max_parallel_runs, cooldown_after_429, rate_limit_policy, enabled) with types and defaults. | Done |
| tasks CRITICAL-01 | tasks.md T006 | Changed T006 dependencies from "None" to "T001". Updated dependency graph and execution order accordingly. | Done |

### HIGH

| ID | Source | Summary | Status |
|----|--------|---------|--------|
| spec HIGH-01 | spec.md FR-010 | Narrowed FR-010 to "REST API only". Mission Control UI deferred to follow-up feature. | Done |
| spec HIGH-02 | spec.md Implementation Scope | Added explicit decision: rate_limit_policy retained as dict[str, Any] in Pydantic model; enum alignment deferred. | Done |
| plan HIGH-01 | plan.md AD-002 | Added UUID-to-string coercion note for owner_user_id, confirmed existing API router handles conversion. | Done |
| plan HIGH-02 | plan.md Section 3.2 | Added step 4 to adapter cleanup: grep audit for metadata["auth_mode"] consumers, with atomic update or backward-compat fallback guidance. | Done |
| plan HIGH-03 | plan.md Risk Mitigation | Added new risk entry for NULL/invalid credential_source in existing DB rows, with verification procedure in T007. | Done |

### MEDIUM

| ID | Source | Summary | Status |
|----|--------|---------|--------|
| tasks MEDIUM-01 | tasks.md T005 | Removed T002 from T005 dependencies (audit is about provider-agnostic guarantee, not auth_mode cleanup). | Done |
| tasks MEDIUM-02 | tasks.md T004 | Added same-file conflict note for T003/T004 edits to test_agent_runtime_models.py. | Done |
| tasks MEDIUM-03 | tasks.md T007 | Added edge case coverage audit step (EC-001, EC-003, EC-004, EC-006, EC-009, EC-010) and DB data integrity check to T007. | Done |
| tasks MEDIUM-04 | tasks.md T004 | Added test_managed_agent_provider_profile_forbids_unknown_fields test case to validate extra="forbid" for arbitrary unknown fields. | Done |

### Infrastructure

| ID | Source | Summary | Status |
|----|--------|---------|--------|
| docs MEDIUM-01 | Constitution XII | Created migration tracker at docs/tmp/remaining-work/127-codex-openrouter-phase3.md. | Done |

---

## Remediations Skipped

| ID | Source | Summary | Reason |
|----|--------|---------|--------|
| spec MEDIUM-01 | spec.md NFR-001, NFR-002 | NFR performance targets (500ms/200ms) have no verification task | Deferred — lightweight performance assertion can be added during T007 execution if time permits. Not a blocking concern for artifact correctness. |
| plan MEDIUM-01 | plan.md T001 field table | Constraint values not fully specified per field in the field table | Partially addressed — the plan references the same constraints as tasks.md T001 step 2 ("min_length=1 on required strings, ge=0/ge=1/ge=60 where applicable"). Full per-field constraint annotation would be redundant with the tasks.md table and is a minor documentation concern. |

---

## Residual Risks

1. **Edge case coverage**: EC-001, EC-003, EC-004, EC-006, EC-009, EC-010 are not yet verified as covered by existing tests. The T007 audit step will confirm coverage; uncovered edge cases should become follow-up tasks.
2. **Metadata consumer breakage**: The plan now requires a grep audit for metadata["auth_mode"] consumers before adapter changes are applied. If consumers exist outside the adapter boundary (e.g., in artifacts.py build_canonical_start_handle), they must be updated atomically or a backward-compat shim added.
3. **DB data integrity**: Old rows with NULL credential_source must be verified before merging. The T007 DB check will confirm zero results; if non-zero, a data-fix procedure must be documented.
4. **Mission Control UI**: FR-010 Mission Control UI coverage is deferred. This is an explicit scope reduction, not a residual risk, but should be tracked as a follow-up feature.
5. **rate_limit_policy type divergence**: The Pydantic model uses dict[str, Any] while the DB uses ManagedAgentRateLimitPolicy enum. This is documented as deferred and is not a blocking risk, but should be addressed in a follow-up type-alignment change.
