# Tasks: Remediation Evidence Bundles

**Input**: `specs/227-remediation-evidence-bundles/spec.md`  
**Plan**: `specs/227-remediation-evidence-bundles/plan.md`  
**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`  
**Integration Test Command**: `./tools/test_integration.sh`

## Source Traceability

MM-452 is preserved in `docs/tmp/jira-orchestration-inputs/MM-452-moonspec-orchestration-input.md` and `spec.md`. Tasks cover FR-001 through FR-012, SC-001 through SC-007, and DESIGN-REQ-006 through DESIGN-REQ-009, DESIGN-REQ-022, and DESIGN-REQ-023.

## Phase 1: Setup

- [X] T001 Confirm MM-452 source input and classify it as a single-story runtime request in `docs/tmp/jira-orchestration-inputs/MM-452-moonspec-orchestration-input.md` and `specs/227-remediation-evidence-bundles/spec.md`.
- [X] T002 Inspect adjacent completed MoonSpec artifacts `specs/221-remediation-context-artifacts` and `specs/222-remediation-evidence-tools` before planning new work.
- [X] T003 Review source design sections 5.3, 6, and 9 in `docs/Tasks/TaskRemediation.md`.

## Phase 2: Foundational

- [X] T004 Confirm existing remediation context and evidence tool boundaries in `moonmind/workflows/temporal/remediation_context.py` and `moonmind/workflows/temporal/remediation_tools.py`.
- [X] T005 Confirm existing focused test harness in `tests/unit/workflows/temporal/test_remediation_context.py`.

## Phase 3: Story

Story: A remediation runtime diagnoses from bounded artifact-first evidence and typed evidence tools, and re-reads current target health before side-effecting action requests.

Independent test: Create or simulate a target execution and linked remediation execution, generate the remediation context, then verify declared context/artifact/log/live-follow reads and pre-action target-health guard behavior while rejecting undeclared or unsafe evidence.

Unit test plan: Use `tests/unit/workflows/temporal/test_remediation_context.py` for context generation, typed evidence access, live-follow gating, and pre-action guard behavior.

Integration test plan: Use `./tools/test_integration.sh` for compose-backed `integration_ci` validation when Docker is available; if Docker is unavailable in the managed container, record the exact blocker in `verification.md`.

- [X] T006 Verify existing context artifact tests cover FR-001 through FR-004, FR-011, FR-012, SC-001, SC-002, SC-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-022, and DESIGN-REQ-023 in `tests/unit/workflows/temporal/test_remediation_context.py`.
- [X] T007 Verify existing typed evidence tests cover FR-005 through FR-009, SC-003, SC-004, DESIGN-REQ-008, and live-follow portions of DESIGN-REQ-009 in `tests/unit/workflows/temporal/test_remediation_context.py`.
- [X] T008 Add failing unit coverage for pre-action current target health and target-change guard reads in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-010, SC-006, and DESIGN-REQ-009.
- [X] T009 Confirm compose-backed integration validation path or blocker for `./tools/test_integration.sh` in `specs/227-remediation-evidence-bundles/verification.md` covering acceptance scenarios 1-6.
- [X] T010 Implement `RemediationActionRequestPreparation` and `RemediationTargetHealthSnapshot` in `moonmind/workflows/temporal/remediation_tools.py`.
- [X] T011 Implement `RemediationEvidenceToolService.prepare_action_request` in `moonmind/workflows/temporal/remediation_tools.py` to validate linked context and re-read current target health without executing actions.
- [X] T012 Export the preparation and target-health models from `moonmind/workflows/temporal/__init__.py`.

## Phase 4: Alignment And Verification

- [X] T013 Run focused unit verification for remediation context/evidence behavior.
- [X] T014 Run traceability check for MM-452 and source design IDs across specs, Jira input, code, and tests.
- [X] T015 Run final `/moonspec-verify` and record verification evidence in `specs/227-remediation-evidence-bundles/verification.md`.

## Dependencies And Order

T001-T003 precede planning and implementation. T008 must fail or identify the missing guard before T010-T012. T009 records integration availability before final verification. T013-T015 run after implementation.

## Parallel Work

T006 and T007 can be reviewed in parallel because they inspect existing verified behavior. T010 and T012 touch different files but T012 depends on the public names introduced by T010.
