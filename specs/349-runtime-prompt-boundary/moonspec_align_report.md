# MoonSpec Alignment Report: Runtime Prompt Boundary

**Source**: MM-650 canonical Jira preset brief preserved in `spec.md`
**Date**: 2026-05-13

## Updated

- `spec.md`: Added stable `SCN-001` through `SCN-004` labels to the existing acceptance scenarios so downstream `plan.md` and `tasks.md` scenario references map directly to the specification.
- `quickstart.md`: Aligned the focused unit-test command with `tasks.md` by including `tests/unit/workflows/adapters/test_base_external_agent_adapter.py` and `tests/unit/workflows/adapters/test_openclaw_agent_adapter.py`.

## Key Decisions

- Scenario traceability: chose to label the existing acceptance scenarios rather than rewrite plan/task references because the scenario meanings were already stable and this preserves the original behavior contract with minimal spec churn.
- Test command drift: chose the broader `tasks.md` unit command for quickstart because task generation is the latest downstream artifact and includes required adapter-boundary coverage from the plan.

## Validation

- `SPECIFY_FEATURE=349-runtime-prompt-boundary .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks`: PASS.
- Artifact validation: PASS. One story remains, `MM-650` and `DESIGN-REQ-026` are preserved, all `SCN-*` labels are present, task IDs remain sequential T001-T033, unit/integration tasks precede implementation, red-first confirmation precedes production tasks, and final `/moonspec-verify` remains present.

## Remaining Risks

- No artifact drift found after remediation. Application implementation and test execution remain future `/speckit.implement` work.
