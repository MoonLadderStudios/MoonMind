# Specification Analysis Report: Integration Test Improvements — Phase 5

| ID  | Category      | Severity | Location(s)       | Summary                                                                 | Recommendation                                                                                   |
| --- | ------------- | -------- | ----------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| C1  | Coverage Gap  | MEDIUM   | spec.md R4, tasks.md T026-T027 | Spec references "activity-topology spec (060) and integrations-monitoring spec (061)" but tasks reference docs by filename, not by spec number 060/061. | Clarify mapping: confirm `docs/Temporal/ActivityCatalogAndWorkerTopology.md` = 060 and `docs/Temporal/IntegrationsMonitoringDesign.md` = 061. No action needed if confirmed. |
| I1  | Inconsistency | LOW      | spec.md vs tasks.md | Spec says "~39 integration test files" but actual count is 31 total integration test files (28 under tests/integration/ + 3 under tests/integration/services+workflows). The ~39 number may include the already-marked files + provider tests. | Update spec.md or plan.md to use the accurate count (31 total integration test files, 4 already marked, ~23 need marking). |
| I2  | Inconsistency | LOW      | plan.md vs tasks.md | Plan says "~15-20 expected to receive integration_ci marker" but tasks.md has 23 files receiving the marker (T003-T025). | Update plan.md scale/scope to say "~23 files" for accuracy.                                      |
| A1  | Ambiguity     | MEDIUM   | tasks.md T030     | "Verify tools/test-provider.ps1 syntax is valid" on a non-Windows (Linux) host — PowerShell syntax check via `pwsh -NoProfile -Command` may not be available. | Clarify: use `pwsh -NoProfile -Command "Get-Content tools/test-provider.ps1 \| Invoke-Expression"` if `pwsh` is installed, or defer to CI/Windows developer verification. |
| D1  | Duplication   | LOW      | spec.md Goals G4, tasks.md | Spec Goal 4 says "Ensure all live Jules/provider tests carry provider_verification + jules + requires_credentials markers" but tasks.md has no task for this — the provider test is already marked correctly per spec's "Already correctly marked" section. | No action needed — this is a no-op since `tests/provider/jules/test_jules_integration.py` already has all three markers. Consider removing from tasks scope or noting explicitly as verified. |

## Coverage Summary

| Requirement Key            | Has Task? | Task IDs     | Notes                                              |
| -------------------------- | --------- | ------------ | -------------------------------------------------- |
| update-agents-md-testing   | Yes       | T002         | Directly addresses R1                              |
| add-powershell-provider-script | Yes    | T001         | Directly addresses R2                              |
| marker-coverage-integration-ci | Yes    | T003-T025    | Directly addresses R3 — 23 files covered           |
| provider-test-marker-verify | N/A (already done) | — | `tests/provider/jules/test_jules_integration.py` already has all 3 markers (D1) |
| update-spec-doc-references | Yes       | T026, T027   | Directly addresses R4                              |
| validation-hermetic-tests  | Yes       | T028         | Success criterion 1                                |
| validation-provider-scripts | Yes      | T029, T030   | Success criterion 2                                |
| validation-agents-md-doc   | Implicit  | T002         | Success criterion 3 (documentary, no runtime test) |
| validation-no-cred-marked-ci | Implicit | Phase 3 exclusion list | Success criterion 4 — explicit exclusion list in tasks.md |
| validation-no-jules-in-required-ci | Yes | T026, T027  | Success criterion 5                                |

## Constitution Alignment Issues

None. All principles PASS. This feature is purely additive (markers, docs, one script).

## Unmapped Tasks

| Task ID | Mapped To          | Notes                                                    |
| ------- | ------------------ | -------------------------------------------------------- |
| T031    | General regression | Not tied to a specific spec requirement but validates no marker regressions. Reasonable as a general quality gate. |

## Metrics

- **Total Requirements**: 5 (R1–R4 + implicit provider marker verification)
- **Total Tasks**: 31 (T001–T031)
- **Coverage %**: 100% — all spec requirements have associated tasks
- **Ambiguity Count**: 1 (A1)
- **Duplication Count**: 1 (D1 — spec goal already satisfied, no task needed)
- **Critical Issues Count**: 0
- **High Issues Count**: 0
- **Medium Issues Count**: 2 (C1, A1)
- **Low Issues Count**: 3 (I1, I2, D1)

## Next Actions

**No CRITICAL or HIGH issues found.** The artifacts are consistent and implementation-ready.

Minor improvements suggested before implementation:
1. **A1 (MEDIUM)**: Clarify T030's PowerShell verification approach for Linux hosts. If `pwsh` isn't installed locally, this task should note that verification happens on CI or a Windows developer machine.
2. **C1 (MEDIUM)**: Confirm that `ActivityCatalogAndWorkerTopology.md` and `IntegrationsMonitoringDesign.md` are indeed the "060" and "061" specs referenced in the spec. If they are not numbered 060/061, update the spec reference.
3. **I1/I2 (LOW)**: Update the file counts in spec.md/plan.md for accuracy (31 integration test files total, ~23 need marking).

These are cosmetic/clarification issues and do **not** block `speckit-implement`.

## Remediation Offer

Would you like me to suggest concrete remediation edits for the top issues (A1, C1, I1/I2)? These are minor text adjustments and do not affect implementation correctness.
