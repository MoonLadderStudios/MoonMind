# Prompt A Remediation Analysis (Second Pass, Post-Remediation)

**Date**: 2026-04-03
**Artifacts reviewed**: `spec.md`, `plan.md`, `tasks.md`, `analysis-v2.md`
**Reviewer**: Qwen Code (Prompt A, second pass)

---

## spec.md

### HIGH-01: Outgoing API response `auth_mode` emission not addressed in spec

- **Severity**: HIGH
- **Artifact**: spec.md
- **Location**: Functional Requirements (FR-007), Edge Cases (EC-008)
- **Problem**: FR-007 says `auth_mode` MUST be removed from the Pydantic model, and EC-008 says legacy values should be "auto-migrated or rejected," but the spec is silent on whether outgoing API responses (e.g., `build_canonical_start_handle` in `artifacts.py` line 2279) should also stop emitting `auth_mode` alongside `credential_source`.
- **Remediation**: Add a sentence to FR-007 or a new FR-015 clarifying that outgoing API responses may retain `auth_mode` as a derived backward-compatibility field computed from `credential_source`, or explicitly require its removal. If retained for backward compat, document the derivation rule: `"oauth" if credential_source == "oauth_volume" else "api_key"`.
- **Rationale**: Without this, implementers and reviewers cannot determine whether `artifacts.py` line 2279's dual emission is correct behavior or a leftover that should be cleaned up in this feature.

### MEDIUM-01: Edge case EC-008 migration mechanism not specified

- **Severity**: MEDIUM
- **Artifact**: spec.md
- **Location**: Edge Cases (EC-008)
- **Problem**: EC-008 says legacy `auth_mode` values should be "auto-migrated or rejected with migration guidance" but does not specify which path (migration vs. rejection) is chosen, nor where the migration logic lives.
- **Remediation**: Resolve EC-008 by stating: "Legacy `auth_mode` values in incoming API payloads are **rejected** by `extra='forbid'` on the Pydantic model. Migration is a one-time DB-level operation, not a per-request transform. No runtime auto-migration code is added."
- **Rationale**: Eliminates implementation ambiguity between adding runtime migration logic vs. relying on Pydantic's `extra="forbid"` to reject legacy input.

### LOW-01: Success criterion SC-005 references `speckit-analyze`

- **Severity**: LOW
- **Artifact**: spec.md
- **Location**: Success Criteria (SC-005)
- **Problem**: SC-005 says "`speckit-analyze` reports no CRITICAL or HIGH consistency gaps," but this is a tooling-dependent criterion, not an intrinsic property of the implementation.
- **Remediation**: Replace with a concrete verification method, e.g., "Manual consistency audit of spec/plan/tasks against DOC-REQ traceability table yields zero unresolved CRITICAL or HIGH gaps."
- **Rationale**: Makes the success criterion independently verifiable without requiring a specific tool.

---

## plan.md

### HIGH-02: Adapter metadata consumer audit does not enumerate `artifacts.py` as a required update target

- **Severity**: HIGH
- **Artifact**: plan.md
- **Location**: Section 3.2, step 4 (Metadata consumer audit)
- **Problem**: The plan instructs a grep audit for `metadata["auth_mode"]` consumers and mentions `artifacts.py` line 2279 as an example, but `artifacts.py` does not consume the adapter's metadata dict — it projects DB rows directly to API response dicts. The plan conflates two different `auth_mode` surfaces: (1) adapter metadata dict, and (2) DB-to-API projection in `artifacts.py`. These require different treatments.
- **Remediation**: Split step 4 into two sub-steps: (a) "Grep for consumers of `metadata['auth_mode']` from the adapter's output dict; update atomically or add backward-compat shim." (b) "Review `build_canonical_start_handle` in `artifacts.py` line ~2279: decide whether to remove the derived `auth_mode` field from API responses or retain it for backward compatibility. Document the decision and update the spec accordingly."
- **Rationale**: The adapter metadata dict and the API response projection are distinct data flows with different backward-compatibility requirements; conflating them risks incorrect or incomplete fixes.

### MEDIUM-02: Strategy `default_auth_mode` property naming not documented as intentional

- **Severity**: MEDIUM
- **Artifact**: plan.md
- **Location**: Section 3.3 (Strategy Default Auth Mapping)
- **Problem**: The plan proposes keeping `default_auth_mode` property name on strategies and mapping return values, but does not explicitly document that the naming retention is intentional (not an oversight) to avoid confusion with FR-007's `auth_mode` removal mandate.
- **Remediation**: Add a note to Section 3.3: "The property name `default_auth_mode` on strategy classes is intentionally retained. It is a legacy name that now returns values compatible with `credential_source` semantics (via mapping). Renaming is deferred to a future cleanup. This does not conflict with FR-007, which targets the `ManagedAgentProviderProfile` Pydantic field, not strategy property names."
- **Rationale**: Prevents implementers from renaming the strategy property as part of this feature, which would be out of scope and risk breaking unrelated callers.

---

## tasks.md

### CRITICAL-01: No task explicitly covers updating `test_managed_agent_adapter.py` for `auth_mode` → `credential_source`

- **Severity**: CRITICAL
- **Artifact**: tasks.md
- **Location**: All tasks (gap across T002, T003, T007)
- **Problem**: `test_managed_agent_adapter.py` contains 16 references to `auth_mode` (lines 157, 158, 185, 193, 194, 222, 230, 238, 247, 404, 628, 678, 725, 758, 793, 1497). T003 is scoped only to `test_agent_runtime_models.py`. T002 covers adapter production code only. T007 will catch failures but does not proactively assign the fix — it only says "Fix any test failures," which is reactive, not prescriptive. With `runtime` mode, leaving test breakage to "fix later" without a dedicated task is a quality gap.
- **Remediation**: Add a new task T003b (or extend T007): "Update `test_managed_agent_adapter.py` — replace all `auth_mode` fixture dict keys with `credential_source`, update assertion at line 185 from `metadata['auth_mode']` to `metadata['credential_source']`, and verify all 16 references are migrated." Add this task between T003 and T004, with a dependency on T002.
- **Rationale**: 16 test references will fail without proactive migration guidance. Assigning this as an explicit task prevents implementation-time guesswork and ensures the adapter test suite is green before the full test run.

### HIGH-03: T002 does not address `artifacts.py` line 2279 dual emission

- **Severity**: HIGH
- **Artifact**: tasks.md
- **Location**: T002 (adapter cleanup)
- **Problem**: T002 covers only the adapter file. The `artifacts.py` projection that emits both `auth_mode` and `credential_source` (line 2279) is mentioned in the plan's metadata consumer audit but has no corresponding task to either remove the legacy field or document its retention.
- **Remediation**: Add a step to T007 (or create T008): "Review `build_canonical_start_handle` in `artifacts.py` line ~2279. If the spec decision is to remove `auth_mode` from outgoing API responses, remove the derived `auth_mode` key from the profile projection dict. If retaining for backward compat, add a comment documenting the derivation rule and the planned removal milestone."
- **Rationale**: The dual emission is a tangible code artifact that must be either cleaned up or explicitly documented as backward compat within this feature's scope.

### MEDIUM-03: T005 grep audit lacks expected-match documentation template

- **Severity**: MEDIUM
- **Artifact**: tasks.md
- **Location**: T005 (Code audit: verify zero provider-specific branching)
- **Problem**: T005 says to run grep commands and "confirm zero matches" but does not specify where to record the findings. Without a designated location, the audit may be performed but not documented, making SC-002 unverifiable.
- **Remediation**: Add a step to T005: "Record grep output (even if empty) as a comment in this task file or in the PR description, confirming zero provider-specific branching matches."
- **Rationale**: Ensures the audit is traceable and satisfies SC-002's verification method requirement.

---

## docs / Cross-Artifact

### MEDIUM-04: No explicit task for DB data integrity check beyond T007 step 7

- **Severity**: MEDIUM
- **Artifact**: tasks.md (cross-artifact)
- **Location**: T007 step 7
- **Problem**: The DB data integrity check (querying `managed_agent_provider_profiles` for NULL or invalid `credential_source`) is listed as step 7 inside T007, which is the "run full test suite" task. This is a data-level verification that should be done early (before or during T001) to confirm no migration is needed, not late as a post-hoc check.
- **Remediation**: Move the DB data integrity check to a new standalone task T000 (or prepend to T001) with "Dependencies: None" and execution before T001, so that any data issues are discovered before code changes are merged.
- **Rationale**: Discovering invalid DB rows after code changes are implemented would require rollback or branching, increasing integration risk.

---

## Analysis-v2 New Issue Assessment

### HIGH-NEW-01: `artifacts.py` line 2279 dual `auth_mode` / `credential_source` emission — ASSESSED

**Confirmed**. The `build_canonical_start_handle` function at line ~2279 of `moonmind/workflows/temporal/artifacts.py` projects DB rows to dicts and emits both:
```python
"auth_mode": "oauth" if row.credential_source.value == "oauth_volume" else "api_key",
"credential_source": row.credential_source.value,
```
This is a backward-compat projection for API responses. The spec's FR-007 mandates `auth_mode` removal from the Pydantic model (incoming validation), but is silent on outgoing responses. This needs an explicit scope decision: retain as derived backward compat (recommended for zero-downtime rollout) or remove in this feature. **Classified as HIGH** — the ambiguity could lead to either premature removal (breaking API consumers) or indefinite retention (violating Principle XIII).

### MEDIUM-NEW-01: `test_managed_agent_adapter.py` line 185 asserts `auth_mode` — ASSESSED

**Confirmed**. The test at line 185 asserts `handle.metadata["auth_mode"] == "oauth"`. After T002 removes `auth_mode` from the adapter's metadata dict, this assertion will fail. The test file contains 16 total `auth_mode` references across fixture dicts and assertions. **Classified as MEDIUM** in analysis-v2, but I am upgrading this to **CRITICAL** because (a) the volume of affected references (16) is significant, (b) no task explicitly covers this file, and (c) T007's reactive "fix any failures" is insufficient guidance for `runtime` mode quality standards.

---

## Summary by Severity

| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL | 1 | CRITICAL-01 |
| HIGH | 3 | HIGH-01, HIGH-02, HIGH-03 |
| MEDIUM | 3 | MEDIUM-01, MEDIUM-02, MEDIUM-03, MEDIUM-04 |
| LOW | 1 | LOW-01 |

---

## Final Determination

### Safe to Implement: NO

### Blocking Remediations

1. **CRITICAL-01**: Add an explicit task covering `test_managed_agent_adapter.py` `auth_mode` → `credential_source` migration (16 references). Without this, the adapter test suite will fail and there is no assigned owner.
2. **HIGH-01**: Clarify in the spec whether outgoing API responses should retain or remove the derived `auth_mode` field. Without this, `artifacts.py` line 2279 cannot be correctly handled.
3. **HIGH-02**: Split the plan's metadata consumer audit into two distinct data flows (adapter metadata dict vs. DB-to-API projection) with separate treatment guidance.
4. **HIGH-03**: Add a task to handle `artifacts.py` line 2279 — either remove the legacy `auth_mode` field from the API response projection or document it as backward compat with a removal plan.

### Determination Dationale

The spec, plan, and tasks are internally consistent on the primary code changes (Pydantic model rewrite and adapter cleanup). However, the `auth_mode` removal creates a downstream impact on `test_managed_agent_adapter.py` (16 references) and leaves ambiguity around the `artifacts.py` API response projection. With the feature in `runtime` mode, unassigned test breakage and unresolved API contract ambiguity are unacceptable for safe implementation. Resolving CRITICAL-01 and the three HIGH items would make this ready to implement.
