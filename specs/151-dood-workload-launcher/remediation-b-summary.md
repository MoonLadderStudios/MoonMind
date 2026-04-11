# Prompt B Remediation Summary

**Input**: `remediation-a.md`

## Remediations Completed

- **HIGH**: Added explicit US1 validation and implementation task coverage for FR-003 / `artifactsDir` in `tasks.md`.
  - T009 now requires Docker run argument tests for approved artifacts directory handling.
  - T014 now requires launcher implementation for approved artifacts directory handling.
- **MEDIUM**: Added explicit US1 validation and implementation task coverage for FR-007 normal-completion cleanup policy in `tasks.md`.
  - T010 now requires normal-completion container removal tests for successful and non-zero exits.
  - T013 now requires cleanup-policy removal after normal completion.

## Remediations Skipped

None.

## Validation

- Runtime task-scope validation passed:

```text
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
Scope validation passed: tasks check (runtime tasks=15, validation tasks=18).
```

## Residual Risks

- `speckit_analyze_report.md` and `remediation-a.md` remain historical pre-remediation artifacts. Re-run `speckit-analyze` and Prompt A if a fresh post-remediation determination is required before implementation.
- The task list now explicitly covers the missing behaviors, but runtime correctness still depends on implementing and running the listed tests.
