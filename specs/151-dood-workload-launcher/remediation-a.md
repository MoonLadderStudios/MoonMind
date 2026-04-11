# Prompt A: Remediation Discovery

**Scope**: `spec.md`, `plan.md`, `tasks.md`, and latest `speckit_analyze_report.md` for `specs/151-dood-workload-launcher/`

## Findings By Artifact

### tasks.md

| Severity | Artifact | Location | Problem | Remediation | Rationale |
| --- | --- | --- | --- | --- | --- |
| HIGH | tasks.md | US1 tasks, T009 and T014 | FR-003 requires workloads to run against the approved artifacts directory, and the contract requires `artifactsDir`, but the concrete US1 tasks only mention workspace/cache mounts and task repo workdir. | Add one US1 validation task in `tests/unit/workloads/test_docker_workload_launcher.py` that asserts `artifactsDir` availability/path handling, and add one US1 implementation task in `moonmind/workloads/docker_launcher.py` for approved artifacts directory mount/path handling. | Implementation could otherwise satisfy workspace execution while silently omitting the artifacts directory required by the runtime contract. |
| MEDIUM | tasks.md | US1/US2 tasks, T013 and T020 | FR-007 requires ephemeral workload containers to be removed after normal completion when cleanup policy requires removal, but the task list only makes timeout/cancel cleanup explicit. | Amend the US1 validation and implementation tasks to explicitly cover removal after successful and failed non-timeout exits, or expand T013/T020 to cover cleanup policy application on normal completion. | Timeout and cancellation cleanup are well-covered, but normal-completion removal is part of the MVP launch path and should not remain implicit. |

### spec.md

No remediation required. Requirements are coherent, measurable, and aligned with the runtime scope.

### plan.md

No remediation required. The plan includes production runtime surfaces, validation strategy, constitution checks, and no detected architecture conflicts.

### latest speckit-analyze output

No remediation required to the analysis artifact. It identifies the same HIGH and MEDIUM task coverage gaps and reports no constitution conflicts or critical issues.

## Runtime And Traceability Gates

- Production runtime code tasks: present in T012-T016, T020-T023, and T027-T030.
- Validation tasks: present in T004-T011, T017-T019, T024-T026, and T032-T035.
- Runtime scope validator: passed with runtime tasks and validation tasks.
- `DOC-REQ-*` identifiers: none present in this feature's `spec.md`, `tasks.md`, or contract artifacts, so DOC-REQ mapping remediation is not applicable.

## Safe to Implement

Safe to Implement: NO

## Blocking Remediations

- HIGH: Add explicit artifacts directory validation and implementation tasks for FR-003 / `artifactsDir` before starting implementation.

## Determination Rationale

The feature has production runtime tasks, validation tasks, passing runtime task-scope validation, and no DOC-REQ obligations, but implementation is not safe to start until the HIGH task coverage gap for the required artifacts directory contract is remediated.
