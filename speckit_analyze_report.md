# Specification Analysis Report

Feature: `171-jira-rollout-hardening`

Analyzed artifacts:
- `specs/171-jira-rollout-hardening/spec.md`
- `specs/171-jira-rollout-hardening/plan.md`
- `specs/171-jira-rollout-hardening/tasks.md`
- `.specify/memory/constitution.md`

## Executive Summary

The Jira rollout hardening artifacts are broadly aligned and implementation-ready. The spec, plan, and tasks all preserve runtime mode, keep Jira browser integration behind the Create-page runtime gate, reuse the trusted server-side Jira boundary, and include both production runtime code changes and validation tests.

One medium-severity coverage gap remains: the session-only remembered project/board behavior is specified as required but is not called out by an explicit implementation or validation task. One low-severity measurable-outcome ambiguity remains around what "responsive for ordinary Jira boards" means.

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| COV-001 | Coverage | MEDIUM | `specs/171-jira-rollout-hardening/spec.md` FR-023; `specs/171-jira-rollout-hardening/tasks.md` | FR-023 requires session-only restoration of the last selected Jira project/board when enabled. The task list includes browser state and provenance work, but no explicit implementation or validation task for session storage behavior. | Add explicit tasks for storing/restoring last project/board only when `rememberLastBoardInSession` is enabled, plus a frontend test covering enabled and disabled behavior. |
| AMB-001 | Ambiguity | LOW | `specs/171-jira-rollout-hardening/spec.md` SC-001; `specs/171-jira-rollout-hardening/plan.md` Performance Goals | The success criterion says the Jira browser remains responsive for ordinary Jira boards, but it does not define a board size, page size, or latency threshold. | Define a bounded scenario such as N columns and M issues, or keep this as an operator-observed acceptance criterion if no hard performance target is needed for this phase. |

## Coverage Summary Table

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 Jira UI block only when enabled | Yes | T005, T006 | Runtime config implementation and unit coverage. |
| FR-002 Runtime endpoints and defaults | Yes | T005, T006 | Endpoint templates and configured defaults are tested. |
| FR-003 Existing Create behavior unchanged when disabled | Yes | T006, T018 | Runtime config and hidden-controls tests cover disabled behavior. |
| FR-004 Server-side Jira browser endpoints through trusted boundary | Yes | T009-T016 | Service, router, and policy tests cover trusted boundary reuse. |
| FR-005 Verify/projects/boards/columns/issues/detail endpoints | Yes | T012-T016 | Router tests cover all endpoint families. |
| FR-006 Policy and project allowlists apply | Yes | T010, T016 | Policy-denied service and router tests. |
| FR-007 No browser credential exposure | Yes | T010, T016 | Redaction and response-shape regression coverage. |
| FR-008 Normalize failures into safe errors | Yes | T014, T023 | Backend and frontend local failure handling. |
| FR-009 Normalize board columns/status mapping | Yes | T009, T012 | Browser service grouping tests and implementation. |
| FR-010 Normalize issue summaries | Yes | T009, T012 | Browser service list normalization. |
| FR-011 Normalize issue detail/import recommendations | Yes | T009, T012 | Detail normalization and recommended import text coverage. |
| FR-012 Jira browser hidden when disabled | Yes | T018 | Frontend disabled-state test. |
| FR-013 Open from preset/step target | Yes | T020, T026, T027 | Browser state and target-specific tests. |
| FR-014 One browser surface at a time | Yes | T020 | Shared browser state task. |
| FR-015 Navigate project/board/column/issue preview | Yes | T021, T022 | Hook/client and browser UI tasks. |
| FR-016 Import modes | Yes | T030, T033, T036 | Import formatter and tests cover modes. |
| FR-017 Replace/append into preset instructions | Yes | T034, T035 | Preset replace and append tests. |
| FR-018 Preserve objective precedence | Yes | T034, T052 | Preset import and submission invariance tests. |
| FR-019 Preset reapply signaling | Yes | T041, T042, T043 | Reapply and conflict message tests plus implementation. |
| FR-020 Step import updates only selected step | Yes | T031, T032 | Step import implementation and regression tests. |
| FR-021 Template-bound step import detaches identity | Yes | T037, T044 | Template-bound detach tests and warning implementation. |
| FR-022 Provenance chip | Yes | T038, T039 | Provenance state and chip test. |
| FR-023 Session-only restore last project/board | Partial | T020 | Browser state exists, but no explicit session-memory implementation or validation task. |
| FR-024 Jira failures remain local/non-blocking | Yes | T014, T023, T054 | Backend safe errors, frontend inline errors, Create-button non-blocking test. |
| FR-025 Submission contract unchanged | Yes | T052 | Explicit submission-payload invariance test. |
| FR-026 Required deliverables include runtime code changes | Yes | T005, T009-T016, T018-T024, T030-T044 | Runtime implementation tasks exist across backend and frontend. |
| FR-027 Required deliverables include validation tests | Yes | T006, T010-T016, T018, T026-T029, T031-T037, T039, T043-T045, T052-T054, T061-T064 | Backend, frontend, and final validation tasks are present. |

Full explicit coverage: 26 / 27 requirements.
Partial explicit coverage: 1 / 27 requirements.

## Task Quality

The task list is dependency ordered and grouped by independently testable user story. It follows test-driven sequencing: tests precede or accompany the production work for runtime config, backend browser endpoints, frontend browsing, import semantics, preset conflict signaling, provenance, failure handling, and submission invariance.

Parallel markers are used on independent tests and code slices, and the later polish/validation phase keeps generated types, lint/build/test validation, and manual quickstart verification distinct from feature implementation.

No tasks appear to be purely spec-only in a way that would violate runtime scope. Cross-cutting documentation/copy tasks are supporting work after production code and tests.

## Constitution Alignment Issues

No constitution violations found.

Relevant checks:
- Runtime behavior is backed by production code tasks and validation tests.
- Jira credential handling remains server-side and policy-aware.
- Existing Create Task submission semantics remain unchanged.
- No workflow or activity payload changes are proposed.
- No compatibility aliases or hidden fallbacks are introduced.

## Unmapped Tasks

- T001-T004 are setup and artifact-review tasks; they support implementation but do not map to a single requirement.
- T055-T064 are generated-type, accessibility, documentation/copy, quickstart, and validation tasks; they map to rollout hardening and final validation rather than a single feature behavior.
- No implementation task appears unrelated to the feature scope.

## Metrics

- Total requirements: 27
- Total tasks: 64
- Full explicit requirement coverage: 96%
- Partial requirement coverage: 4%
- Ambiguity findings: 1
- Duplication findings: 0
- Critical findings: 0

## Next Actions

- Resolve COV-001 before implementation by adding explicit FR-023 session-memory implementation and validation tasks.
- Optionally resolve AMB-001 by adding a bounded board-size or latency criterion.
- After remediation, rerun `speckit-analyze` or proceed to implementation if the team accepts the medium coverage gap.
