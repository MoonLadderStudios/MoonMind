# Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
| --- | --- | --- | --- | --- | --- |
| C1 | Coverage Gap | HIGH | `spec.md`: FR-003; `contracts/workload-launcher-contract.md`: Input Contract; `tasks.md`: US1 tasks | `artifacts_dir` / `artifactsDir` is required by the spec and contract, but the task list only names workspace/cache mounts and task repo workdir. No concrete task requires validating or implementing approved artifacts directory availability or mounting. | Add at least one US1 validation task for `artifactsDir` handling and one US1 implementation task for approved artifacts directory mount/path handling in `moonmind/workloads/docker_launcher.py`. |
| C2 | Coverage Gap | MEDIUM | `spec.md`: FR-007; `tasks.md`: US1 and US2 tasks | Ephemeral container removal after normal completion is part of US1 and FR-007, but the explicit cleanup tasks focus on timeout/cancel and Docker cleanup utilities. Normal-completion removal is implied by story text, not directly assigned to an implementation task. | Amend US1 tasks to include a normal-completion removal test and launcher implementation step, or expand T013/T020 language to cover removal after successful and failed exits when cleanup policy requires it. |

## Coverage Summary

| Requirement Key | Has Task? | Task IDs | Notes |
| --- | --- | --- | --- |
| FR-001 validated-profile-requests-only | Yes | T001, T002, T005, T011, T016 | Activity tests and binding cover request validation through the Phase 1 profile contract. |
| FR-002 deterministic-identity-labels | Yes | T004, T009, T012 | Covered by launcher construction tests and implementation. |
| FR-003 repo-and-artifacts-directories | Partial | T009, T014 | Repo/workdir coverage is explicit; artifacts directory handling is missing from concrete task wording. |
| FR-004 workspace-and-approved-cache-mounts | Yes | T009, T014 | Workspace and approved cache mount coverage is explicit. |
| FR-005 profile-and-request-limits | Yes | T009, T012, T013 | Env, network, timeout, resource, entry, and command behavior are covered by launcher tests and implementation. |
| FR-006 bounded-result-metadata | Yes | T004, T010, T013 | Bounded stdout/stderr, exit status, timing, profile, image, and diagnostics are covered. |
| FR-007 remove-ephemeral-containers-on-completion | Partial | T020 | Cleanup utility exists, but normal-completion removal is not explicit in US1 implementation or validation tasks. |
| FR-008 bounded-timeout-cancel-cleanup | Yes | T017, T018, T020, T021, T022 | Timeout and cancellation cleanup are covered directly. |
| FR-009 label-based-cleanup-lookup | Yes | T019, T023 | Orphan lookup by MoonMind labels is covered. |
| FR-010 distinct-docker-workload-capability | Yes | T006, T007, T024, T025, T027, T028 | Activity catalog and worker topology tasks cover capability routing. |
| FR-011 no-managed-session-overload | Yes | T026, T029, T030 | Worker runtime and activity runtime tasks cover separation from managed-session controllers and verbs. |
| FR-012 runtime-code-plus-validation-tests | Yes | T004-T030, T032-T035 | Runtime implementation and validation commands are explicit. |

## Constitution Alignment Issues

No constitution conflicts were found. The plan includes the required Constitution Check and Post-Design Recheck, keeps implementation tracking in `docs/tmp/remaining-work/`, and preserves the managed-session/workload boundary.

## Unmapped Tasks

- T003: Setup review task for quickstart acceptance commands; does not map to a functional requirement but is appropriate setup work.
- T031: Documentation tracking update under `docs/tmp/remaining-work/`; maps to Constitution XII rather than a feature runtime requirement.
- T034 and T035: Scope validation commands; map to workflow quality gates rather than a single functional requirement.

## Metrics

- Total Requirements: 12
- Total Tasks: 35
- Broad Coverage: 12/12 requirements have at least partial task coverage
- Full Coverage: 10/12 requirements have explicit implementation and validation coverage
- Coverage %: 83.3% fully covered, 100% at least partially covered
- Ambiguity Count: 0
- Duplication Count: 0
- Critical Issues Count: 0

## Next Actions

- Resolve C1 before implementation by adding explicit artifacts directory validation and implementation tasks.
- Resolve C2 before or during US1 implementation by making normal-completion removal explicit in tests and launcher implementation.
- After task remediation, rerun `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and rerun this analysis if the task structure changes materially.
