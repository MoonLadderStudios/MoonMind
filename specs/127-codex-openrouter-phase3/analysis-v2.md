# speckit-analyze Report (v2): 127-codex-openrouter-phase3

**Date**: 2026-04-03
**Artifacts reviewed**: `spec.md`, `plan.md`, `tasks.md`, `analysis.md` (original), `remediation-b-summary.md`
**Verdict**: Proceed with minor residual concerns (0 CRITICAL, 1 HIGH, 3 MEDIUM remaining)

---

## 1. CRITICAL Finding Resolution

### C-01 (OAuthProviderSpec scoping) -- RESOLVED

The spec's OQ-004 now includes a definitive answer: `OAuthProviderSpec.auth_mode` is semantically distinct (OAuth session type for terminal/bootstrap flows) from `ManagedAgentProviderProfile.auth_mode` (credential sourcing strategy). The out-of-scope section explicitly lists `OAuthProviderSpec` and `providers/registry.py` entries. I confirmed in the code that `OAuthProviderSpec.auth_mode: str` in `providers/base.py` and the 3 registry entries (`gemini_cli`, `codex_cli`, `claude_code`) with `auth_mode="oauth"` are indeed a separate concept -- they describe OAuth session transport, not credential sourcing. This is correctly scoped out.

### C-02 (Fields retained as-is) -- RESOLVED

Plan AD-002 now includes a complete "Fields retained as-is" table listing all 12 existing fields with their types and defaults. This eliminates the risk of an implementer accidentally dropping `volume_ref`, `account_label`, or `rate_limit_policy`.

### C-03 (T006 dependency on T001) -- RESOLVED

T006 now declares `Dependencies: T001` (previously "None"). The dependency graph and execution order are both updated. The integration tests construct profiles with `credential_source` and `runtime_materialization_mode`, which require T001's Pydantic changes.

---

## 2. HIGH Finding Resolution

### H-01 (owner_user_id type coercion) -- RESOLVED

Plan AD-002 now includes a note explaining that `owner_user_id` is `str | None` in Pydantic vs `Mapped[Optional[UUID]]` in DB, and that the existing API router already handles the conversion. No new coercion logic is introduced.

### H-02 (rate_limit_policy type divergence) -- RESOLVED

The spec's Implementation Scope section now explicitly documents that `rate_limit_policy` is retained as `dict[str, Any]` in the Pydantic model, with enum alignment deferred to a follow-up change. Plan AD-002's retained-fields table notes this with a cross-reference.

### H-03 (metadata consumer audit for auth_mode removal) -- RESOLVED (with residual concern)

The plan (Section 3.2, step 4) now requires a grep audit for consumers of `metadata["auth_mode"]` before adapter changes, with guidance to update atomically or add a backward-compat shim. T002 step 4 was added to reflect this.

**Verification**: A grep for `metadata["auth_mode"]` confirms at least two consumers:
1. `artifacts.py` line 2279 -- `build_canonical_start_handle` emits `"auth_mode": "oauth" if row.credential_source.value == "oauth_volume" else "api_key"`. This is a DB-to-dict projection, not a metadata consumer per se, but it shows the `auth_mode` key is still expected in API responses.
2. `test_managed_agent_adapter.py` line 185 -- `assert handle.metadata["auth_mode"] == "oauth"`. This test will break when T002 removes `auth_mode` from the metadata dict.

The plan correctly flags this as requiring atomic updates or backward-compat shims. The remediation provides the right guidance but does not enumerate all consumers. This is acceptable as an implementation-time audit, not an artifact gap.

### H-04 (data migration verification for existing DB rows) -- RESOLVED

T007 now includes a DB data integrity check (step 7): query for rows where `credential_source IS NULL` or not in valid values. Plan Risk Mitigation also documents the verification procedure.

---

## 3. MEDIUM Finding Resolution

### M-01 (T001 constraint values) -- PARTIALLY RESOLVED

T001 step 2 now includes a parenthetical specifying constraints: `priority ge=0, max_parallel_runs ge=1, max_lease_duration_seconds ge=60, cooldown_after_429 ge=0`. Required string fields implicitly get `min_length=1`. This is sufficient for implementation.

### M-02 (T005 unnecessary dependency on T002) -- RESOLVED

T005 now declares `Dependencies: T001` (T002 removed). Correct -- the provider-agnostic grep audit is about branching, not auth_mode cleanup.

### M-03 (T003/T004 same-file conflict) -- RESOLVED

T004 now includes a note: "T003 and T004 both edit `tests/unit/schemas/test_agent_runtime_models.py`. Execute atomically."

### M-04 (unknown fields test) -- RESOLVED

T004 test 5 (`test_managed_agent_provider_profile_forbids_unknown_fields`) was added. It passes an arbitrary unknown field alongside valid required fields and asserts `ValidationError`.

### M-05 (edge case coverage) -- RESOLVED

T007 step 6 now explicitly lists EC-001, EC-003, EC-004, EC-006, EC-009, EC-010 and requires confirming they are covered by existing tests, with documentation of follow-ups if not.

### M-06 (NFR performance verification) -- ACCEPTED AS DEFERRED

Per the remediation summary, this is explicitly deferred. Not re-raising.

---

## 4. New Issues Introduced by Remediation

### HIGH-NEW-01: artifacts.py line 2279 still emits legacy `auth_mode` in API response

The `build_canonical_start_handle` function in `artifacts.py` (line 2279) projects DB rows to dicts and includes:
```python
"auth_mode": "oauth" if row.credential_source.value == "oauth_volume" else "api_key",
"credential_source": row.credential_source.value,
```

This function emits **both** `auth_mode` and `credential_source` in the API response. The plan's adapter cleanup (T002 step 3) removes `auth_mode` from the adapter's metadata dict but does not address this artifacts.py projection. If the REST API response schema (which the spec says FR-010 must support) includes `auth_mode` as a field, this is fine for backward compatibility. But the spec's FR-007 says `auth_mode` MUST be removed, and the Pydantic model's `extra="forbid"` will reject `authMode` in incoming payloads. The outgoing projection still emits it.

This is not necessarily a bug -- the artifacts.py function is projecting DB data for API responses, and including both old and new keys is a valid backward-compat strategy. But the spec should clarify whether outgoing API responses should also drop `auth_mode`, or whether it is retained temporarily for API consumers. The remediation's plan says "Mission Control UI deferred to follow-up," which implies API consumers may still need `auth_mode` in responses.

**Recommendation**: Either (a) explicitly document in the spec that outgoing API responses retain `auth_mode` as a derived field for backward compatibility while incoming requests reject it, or (b) add a task to clean up artifacts.py line 2279 in the same PR.

### MEDIUM-NEW-01: Test at test_managed_agent_adapter.py line 185 asserts `metadata["auth_mode"]`

This test will fail after T002 removes `auth_mode` from the metadata dict. T003's scope is limited to `test_agent_runtime_models.py` (the Pydantic schema tests) and does not cover adapter tests. T007 (full test suite) will catch this failure, but the task list should proactively note that adapter tests need updating.

**Recommendation**: Add a note to T002 or T007: "Update `test_managed_agent_adapter.py` assertions that check `metadata['auth_mode']` to check `metadata['credential_source']` instead."

### MEDIUM-NEW-02: Strategy `default_auth_mode` property naming

The plan (Section 3.3) proposes keeping the `default_auth_mode` property name on strategies and adding a mapping layer. The adapter currently calls `_strategy.default_auth_mode` (line 208 of `managed_agent_adapter.py`). The plan suggests mapping the return value but not renaming the property. This is a reasonable backward-compat choice, but the spec's FR-007 says `auth_mode` MUST be removed. The strategy property name is not the same as the Pydantic field, but the shared terminology could cause confusion during implementation.

**Recommendation**: Add a comment in the plan noting this is an intentional naming retention for backward compatibility, with a rename deferred to a future cleanup.

---

## 5. Artifact Consistency After Edits

| Check | Status |
|-------|--------|
| Spec FR-007 (remove auth_mode, add fields) matches plan AD-002 field additions | PASS |
| Plan AD-001 (remove auth_mode entirely) matches spec OQ-001 recommendation | PASS |
| Plan AD-002 retained-fields table matches spec Implementation Scope | PASS |
| Tasks T001 field table matches plan AD-002 field additions | PASS |
| T002 adapter changes match plan Section 3.2 | PASS |
| T003/T004 test updates match plan Section 3.4 | PASS |
| T005 dependency graph (T001 only) matches plan AD-003 | PASS |
| T006 dependency (T001) corrected from original "None" | PASS |
| T007 edge case audit + DB check matches remediation commitments | PASS |
| DOC-REQ traceability consistent across all three artifacts | PASS |
| Migration tracker (`specs/127-codex-openrouter-phase3/spec.md`) created per Constitution XII | PASS |

---

## 6. Summary of Remaining Findings

| Severity | ID | Summary |
|----------|----|---------|
| HIGH | H-NEW-01 | `artifacts.py` line 2279 still emits legacy `auth_mode` in API response -- scope (in or out) needs explicit documentation |
| MEDIUM | M-NEW-01 | `test_managed_agent_adapter.py` line 185 asserts `metadata["auth_mode"]` -- needs updating, not covered by T003 |
| MEDIUM | M-NEW-02 | Strategy `default_auth_mode` property name retained -- intentional but should be documented |

---

## 7. Implementation Readiness

### Overall: Ready with minor clarifications needed

All CRITICAL and HIGH findings from the original analysis are resolved. The remediation is thorough and the artifacts are now internally consistent. Two minor new issues were surfaced by the remediation (adapter test updates and artifacts.py projection), but these are implementation-time concerns, not artifact gaps.

### Remaining actions before implementation:

1. Clarify whether `artifacts.py` line 2279's `auth_mode` emission is intentional backward compat or should be removed in this feature. (HIGH)
2. Note that `test_managed_agent_adapter.py` needs `metadata["auth_mode"]` assertions updated to `metadata["credential_source"]`. (MEDIUM)
3. Document that `default_auth_mode` property naming on strategies is intentionally retained. (MEDIUM)
