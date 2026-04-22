# Tasks: Remediation Evidence Tools

**Input**: `specs/222-remediation-evidence-tools/spec.md`

**Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`

## Setup

- [X] T001 Confirm MM-433 source story and adjacent remediation slices in `specs/220-remediation-create-links` and `specs/221-remediation-context-artifacts`.
- [X] T002 Review `docs/Tasks/TaskRemediation.md` sections 9.5, 9.6, and 11 for evidence-tool and live-follow boundaries.

## Implementation

- [X] T003 Add unit coverage for context-declared artifact reads and taskRunId-scoped log reads.
- [X] T004 Add unit coverage for live-follow gating and cursor handoff.
- [X] T005 Implement `RemediationEvidenceToolService` with typed context, artifact, log, and live-follow methods.
- [X] T006 Export the service and typed result models from `moonmind.workflows.temporal`.
- [X] T007 Verify unsupported evidence, undeclared artifacts, undeclared task runs, and unsupported live follow fail fast.

## Verification

- [X] T008 Run focused unit verification.
- [X] T009 Record final verification evidence in `verification.md`.
