# Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| C1 | Coverage Gap | MEDIUM | spec.md:L81, spec.md:L98, spec.md:L119, tasks.md:L133-L142, tasks.md:L152-L156 | The spec repeatedly requires non-secret diagnostics, but the task list mostly validates that diagnostics exist. It does not call out a direct negative assertion that denial/result metadata excludes raw secrets, broad environment dumps, or auth material. | Add or refine a US4 validation task to assert workload metadata and tool failure details redact or omit secret-like env/auth values. |
| A1 | Ambiguity | LOW | tasks.md:L32-L36 | Foundational tasks use "Add or confirm", which can be interpreted as a no-op during implementation. The runtime scope gate still passes because later story tasks include concrete runtime changes and tests. | Prefer "Implement or verify with failing test coverage" wording if tasks are regenerated, so implementers do not skip behavior that only appears partially present. |

## Coverage Summary Table

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 runner-profile allowlists and registry policy | Yes | T009, T014, T019 | Covered by registry allowlist tests and implementation. |
| FR-002 reject unsafe workload requests | Yes | T010, T011, T012, T015, T016, T017, T019 | Covers unknown profile, env, mount, resource, and unsafe runtime options. Missing fleet capability is covered under US4. |
| FR-003 image provenance | Yes | T009, T014, T019 | Covered. |
| FR-004 non-privileged posture | Yes | T012, T017, T019 | Covered. |
| FR-005 no host networking by default | Yes | T012, T017, T019 | Covered. |
| FR-006 no implicit device access | Yes | T012, T015, T019 | Covered. |
| FR-007 no automatic auth volume inheritance | Yes | T010, T015, T019 | Covered. |
| FR-008 per-profile concurrency | Yes | T020, T021, T024, T025, T028 | Covered. |
| FR-009 fleet-level capacity control | Yes | T022, T026, T028 | Covered. |
| FR-010 ownership and expiration labels | Yes | T029, T032, T035 | Covered. |
| FR-011 orphan cleanup | Yes | T030, T031, T033, T034, T035 | Covered. |
| FR-012 operator-facing diagnostics | Yes | T011, T013, T023, T036, T039, T040, T042 | Covered, with C1 recommending stronger secret-negative validation. |
| FR-013 durable artifacts and bounded metadata | Yes | T036, T039, T040, T042 | Covered. |
| FR-014 executable tool path | Yes | T013, T018, T037, T040 | Covered through tool bridge and activity boundary tasks. |
| FR-015 session/workload identity separation | Yes | T036, T039, T040, T042 | Covered through metadata and activity-boundary validation. |
| FR-016 production runtime code changes | Yes | T014-T018, T024-T027, T032-T034, T039-T041 | Covered. |
| FR-017 validation tests | Yes | T009-T013, T020-T023, T029-T031, T036-T038, T045-T046 | Covered. |

## Constitution Alignment Issues

None. The plan includes initial and post-design Constitution checks, preserves the executable-tool boundary, keeps artifacts/metadata as durable truth, uses runtime validation, and avoids compatibility aliases.

## Unmapped Tasks

- T001-T003 are setup/verification tasks and do not map to a single functional requirement.
- T043-T047 are polish/final verification tasks and intentionally cover cross-cutting readiness rather than one isolated requirement.

## Metrics

- Total Requirements: 17
- Total Tasks: 47
- Coverage %: 100%
- Ambiguity Count: 1
- Duplication Count: 0
- Critical Issues Count: 0

## Next Actions

- No critical issues block `speckit-implement`.
- Recommended pre-implementation improvement: refine or add one US4 test task to explicitly assert that workload diagnostics do not include raw secrets, broad environment dumps, or inherited auth material.
- Suggested next step: run `speckit-implement` when ready, or make the small task wording refinement first if stricter diagnostic-redaction traceability is desired.
