# Prompt A: Remediation Discovery

**Scope**: `spec.md`, `plan.md`, `tasks.md`, and latest `speckit_analyze_report.md` for `specs/171-jira-rollout-hardening/`.

## Inputs

- `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- `specs/171-jira-rollout-hardening/spec.md`
- `specs/171-jira-rollout-hardening/plan.md`
- `specs/171-jira-rollout-hardening/tasks.md`
- `speckit_analyze_report.md`

## Findings

| Severity | Artifact | Location | Problem | Remediation | Rationale |
| --- | --- | --- | --- | --- | --- |
| MEDIUM | `tasks.md` | `tasks.md` lines 56-70; `speckit_analyze_report.md` COV-001 | FR-023 requires optional session-only restoration of the last selected Jira project and board, but the task plan only includes general browser state and lacks explicit implementation and validation tasks for session storage behavior. | Add one frontend implementation task for storing/restoring last selected project and board only when `rememberLastBoardInSession` is enabled, and one frontend test task covering enabled, disabled, and unavailable browser-storage behavior in `frontend/src/entrypoints/task-create.test.tsx`. | Without explicit tasks, implementers may complete the browser state work while skipping the configured session-memory behavior specified in `spec.md`. |
| LOW | `plan.md` | `plan.md` line 18; `spec.md` line 139; `speckit_analyze_report.md` AMB-001 | The phrase "responsive for ordinary Jira boards" is not measurable because the artifacts do not define board size, issue count, page size, or latency threshold. | Either define a bounded scenario, such as expected behavior for a specific number of columns and issues, or restate the outcome as observational rollout guidance instead of a measurable performance requirement. | A concrete bound would make performance validation easier, but the ambiguity does not block implementation because the plan already requires bounded pagination and prompt failure handling. |

## Artifact Review

### spec.md

No blocking remediation required. The specification is runtime-scoped, requires production runtime code and validation tests, preserves trusted Jira boundaries, and contains no `DOC-REQ-*` identifiers.

### plan.md

No blocking remediation required. The plan identifies production backend and frontend surfaces, validation strategy, runtime constraints, and constitution checks. The only plan-level remediation is the low-severity measurable-performance clarification listed above.

### tasks.md

No blocking remediation required. The task list includes production runtime code tasks and validation tasks. The only task-level remediation is the medium coverage gap for explicit session-only project/board memory tasks.

### latest speckit-analyze output

No blocking remediation required. The latest analysis reports no critical findings and no constitution conflicts, with 26 of 27 requirements fully covered and FR-023 partially covered.

## Runtime And Traceability Gates

- Production runtime code tasks: present. Runtime scope validation passed with `runtime tasks=28`.
- Validation tasks: present. Runtime scope validation passed with `validation tasks=28`.
- Runtime scope validator: passed with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- `DOC-REQ-*` identifiers: absent from the active feature artifacts.
- Missing `DOC-REQ-*` mappings: not applicable.

## Safe to Implement

Safe to Implement: YES

## Blocking Remediations

None.

## Determination Rationale

Implementation is safe to start because runtime-mode critical gates pass, production runtime code tasks and validation tasks are present, no `DOC-REQ-*` mapping obligations exist, and Prompt A found no CRITICAL or HIGH issues.
