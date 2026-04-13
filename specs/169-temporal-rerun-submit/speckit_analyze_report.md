# Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| C1 | Coverage Gap | MEDIUM | spec.md:L70; tasks.md:L13-L29 | FR-002 requires rerun mode to win when both rerun and edit identifiers are present, but tasks only review existing mode helpers and do not explicitly add or verify this precedence behavior. | Add a validation task for route precedence in `frontend/src/entrypoints/task-create.test.tsx`, and add an implementation task only if the existing helper does not already satisfy it. |
| C2 | Coverage Gap | MEDIUM | spec.md:L79-L91; data-model.md:L72-L74; contracts/temporal-rerun-submit.openapi.yaml:L209-L221; tasks.md:L69-L77 | The data model and contract include `sourceWorkflowId` as artifact lineage context, but tasks focus on replacement `inputArtifactRef`, redirect, and notice copy; no task explicitly implements or validates `sourceWorkflowId` propagation. | Either add tasks to propagate and test `sourceWorkflowId` during replacement artifact creation, or remove/relax `sourceWorkflowId` from the contract/data model if current runtime lineage is intentionally limited to execution update response and artifact refs. |
| U1 | Underspecification | LOW | spec.md:L78-L79; tasks.md:L75-L77 | "Latest run view or run-chain result" and "enough rerun lineage metadata" are directionally clear but do not define the minimum operator-visible fields beyond workflow id, replacement artifact, and success copy. | During remediation, clarify whether the initial slice requires only returned workflow id plus replacement artifact ref, or additional run id/latest-run indicators in the UI. |

## Coverage Summary

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 support terminal rerun submission | Yes | T009, T012, T013, T015 | MVP path is covered. |
| FR-002 rerun precedence over edit/create | Partial | T001 | Existing helper review is present, but no explicit route-precedence validation task. |
| FR-003 reuse Temporal draft reconstruction | Yes | T001, T002, T005, T024 | Covered through shared form and reconstruction failure tests. |
| FR-004 validate workflow type and rerun capability | Yes | T005, T024, T026, T027 | Covered by rerun capability and reconstruction/unsupported tests. |
| FR-005 use `RequestRerun` | Yes | T007, T009, T011, T013, T015 | Covered at helper, UI request, and backend contract levels. |
| FR-006 preserve edit vs rerun distinction | Yes | T007, T009, T013, T015 | Covered through update-name split. |
| FR-007 artifact-safe preparation | Yes | T016, T019, T022 | Covered for replacement input artifacts. |
| FR-008 no historical artifact mutation | Yes | T016, T019, T022 | Covered by replacement artifact assertions. |
| FR-009 return to Temporal context | Yes | T017, T021, T022 | Covered through redirect tasks. |
| FR-010 expose latest run context | Partial | T017, T020, T021 | Covered by success copy/redirect, but latest-run minimum is not fully specified. |
| FR-011 preserve rerun lineage metadata | Partial | T016, T017, T018, T019, T021 | Replacement artifact and result workflow are covered; `sourceWorkflowId` propagation is not explicit. |
| FR-012 surface rejections without redirect | Yes | T023, T025, T027 | Covered by stale rejection tests and implementation. |
| FR-013 no queue-era fallback | Yes | T010, T014, T027 | Covered by no-create/no-fallback assertions and update route use. |
| FR-014 regression coverage comparing edit/rerun | Yes | T007, T009, T010, T016, T017, T023, T029-T031 | Covered by targeted and final validation tasks. |
| FR-015 runtime code plus tests | Yes | T012-T014, T019-T021, T025-T026, T029-T032 | Runtime scope validation already passes. |

## Constitution Alignment Issues

None found. The plan preserves explicit Temporal contracts, avoids queue compatibility fallback, includes validation tasks, and keeps runtime work within established module boundaries.

## Unmapped Tasks

- T003 and T004 are setup/review tasks that support implementation readiness rather than mapping directly to one functional requirement.
- T028-T032 are cross-cutting validation tasks and intentionally map to final quality gates rather than a single user story.

## Metrics

- Total Requirements: 15
- Total Tasks: 32
- Coverage %: 100% with partial coverage noted for 3 requirements
- Ambiguity Count: 1
- Duplication Count: 0
- Critical Issues Count: 0

## Next Actions

- No CRITICAL or HIGH issues block implementation.
- Before `speckit-implement`, consider remediating C1 and C2 so route precedence and lineage-source metadata are explicitly covered in tasks.
- If the initial runtime slice intentionally excludes explicit `sourceWorkflowId` propagation, update `data-model.md` and the OpenAPI contract to avoid implying a required lineage field that tasks do not implement.
