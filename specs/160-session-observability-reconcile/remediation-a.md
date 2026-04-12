# Prompt A: Remediation Discovery

**Scope**: `spec.md`, `plan.md`, `tasks.md`, and latest `speckit_analyze_report.md`
**Mode**: runtime

## Runtime and Traceability Gates

- Runtime scope gate: PASS. `validate-implementation-scope.sh --check tasks --mode runtime` reported 12 runtime tasks and 12 validation tasks.
- Production runtime code tasks: PRESENT. Runtime tasks target `moonmind/workflows/temporal/**`.
- Validation tasks: PRESENT. Validation tasks target `tests/unit/workflows/temporal/**`.
- `DOC-REQ-*` identifiers: NONE FOUND. No DOC-REQ mapping remediation is required.

## Remediations by Artifact

### spec.md

| Severity | Artifact | Location | Problem | Remediation | Rationale |
| --- | --- | --- | --- | --- | --- |
| MEDIUM | spec.md | `Functional Requirements` FR-007 and `Key Entities` Recurring Reconcile Trigger | The durable recurring trigger is required, but the spec does not define the stable schedule identity, cadence configuration source, default cadence, or enable/disable behavior. | Add a bounded requirement or entity rule naming the schedule ID, cadence configuration source/default, and disabled behavior. | Concrete schedule semantics prevent divergent client helper and test implementations. |

### plan.md

| Severity | Artifact | Location | Problem | Remediation | Rationale |
| --- | --- | --- | --- | --- | --- |
| MEDIUM | plan.md | `Technical Context` and `Implementation surfaces` | The plan states cadence is controlled by existing settings and that client schedule wiring will be added, but it does not name the concrete setting, default cadence, or schedule ID. | Add implementation details for the schedule helper: setting/env name, default cadence, stable schedule ID, workflow ID pattern, and idempotent create/update behavior. | Implementation needs a single source of truth for schedule construction and tests. |

### tasks.md

| Severity | Artifact | Location | Problem | Remediation | Rationale |
| --- | --- | --- | --- | --- | --- |
| MEDIUM | tasks.md | T016, T021, T022 | FR-008 requires stale degraded session checks and orphaned runtime container checks, but tasks only generally mention bounded reconcile output and delegation. | Refine the US3 validation and implementation tasks to explicitly cover stale degraded record detection and orphaned container detection through the controller/activity boundary. | Explicit task coverage reduces the risk of shipping a reconcile wrapper that reports counts but omits one required detection path. |
| MEDIUM | tasks.md | T019, T024 | The schedule tests and helper tasks require idempotent create/update behavior but do not explicitly require the concrete schedule ID, cadence config/default, or enable/disable semantics. | Refine T019 and T024 to include schedule ID, cadence configuration/default, and disabled schedule behavior once the plan/spec define them. | The task list should force validation of the same concrete schedule contract that implementation uses. |

## Safe to Implement

Safe to Implement: YES

## Blocking Remediations

None. No CRITICAL or HIGH remediations were found.

## Determination Rationale

Implementation is safe to start because runtime code tasks and validation tasks are present, there are no DOC-REQ mapping obligations, no constitution conflicts were found, and the remaining remediations are medium specificity improvements rather than blockers.
