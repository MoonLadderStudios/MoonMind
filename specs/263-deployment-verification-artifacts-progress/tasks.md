# Tasks: Deployment Verification, Artifacts, and Progress

**Input**: `specs/263-deployment-verification-artifacts-progress/spec.md`, `specs/263-deployment-verification-artifacts-progress/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/deployment-verification-evidence.md`, `quickstart.md`
**Unit test command**: `pytest tests/unit/workflows/skills/test_deployment_update_execution.py -q`
**Integration test command**: `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q`

**Source Traceability**: `MM-521`; FR-001 through FR-009; SCN-001 through SCN-006; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-005.

## Phase 1: Setup

- [X] T001 Verify active feature pointer `.specify/feature.json` references `specs/263-deployment-verification-artifacts-progress`. (FR-009)
- [X] T002 Confirm existing deployment execution evidence in `moonmind/workflows/skills/deployment_execution.py`, `tests/unit/workflows/skills/test_deployment_update_execution.py`, and `tests/integration/temporal/test_deployment_update_execution_contract.py`. (FR-001, FR-004, FR-005)

## Phase 2: Foundational

- [X] T003 Add deployment verification status, audit, redaction, and progress helper test coverage to `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-001 through FR-008)
- [X] T004 Add deployment dispatch progress/evidence assertions to `tests/integration/temporal/test_deployment_update_execution_contract.py`. (FR-004, FR-008, SC-006)

## Phase 3: Deployment Verification Evidence

**Story**: Gate deployment success on proven verification while preserving redacted durable evidence, audit metadata, and progress states.

**Independent Test**: Invoke the typed deployment update executor with fake runner, artifact writer, and context, then assert success gating, failed and partially verified outcomes, required artifact refs, redacted artifact payloads, audit metadata, and progress lifecycle messages.

**Traceability IDs**: FR-001 through FR-009; DESIGN-REQ-001 through DESIGN-REQ-005.

### Tests First

- [X] T005 [P] Add failing unit tests for partial verification status and unsupported status fail-fast behavior in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-001, FR-002, FR-003, DESIGN-REQ-001, DESIGN-REQ-002)
- [X] T006 [P] Add failing unit tests for all final statuses returning required artifact refs in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-004, FR-005, SC-001, SC-002)
- [X] T007 [P] Add failing unit tests for audit metadata in verification evidence and final outputs in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-006, DESIGN-REQ-003, SC-004)
- [X] T008 [P] Add failing unit tests for recursive evidence redaction in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-007, DESIGN-REQ-004, SC-003)
- [X] T009 [P] Add failing unit tests for bounded progress lifecycle states and messages in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-008, DESIGN-REQ-005, SC-005)
- [X] T010 Add failing integration assertion for progress metadata and artifact refs through `deployment.update_compose_stack` dispatch in `tests/integration/temporal/test_deployment_update_execution_contract.py`. (FR-004, FR-008, SC-006)

### Implementation

- [X] T011 Extend `ComposeVerification` and status derivation in `moonmind/workflows/skills/deployment_execution.py` to support `PARTIALLY_VERIFIED` and fail closed on unsupported status. (FR-001, FR-002, FR-003)
- [X] T012 Add audit metadata construction and attach audit data to evidence payloads and final outputs in `moonmind/workflows/skills/deployment_execution.py`. (FR-006)
- [X] T013 Add recursive evidence redaction before artifact writes in `moonmind/workflows/skills/deployment_execution.py`. (FR-007)
- [X] T014 Add bounded lifecycle progress events and terminal state metadata in `moonmind/workflows/skills/deployment_execution.py`. (FR-008)
- [X] T015 Preserve existing evidence completeness checks and update structured output assembly in `moonmind/workflows/skills/deployment_execution.py`. (FR-004, FR-005)

### Validation

- [X] T016 Run focused unit tests: `pytest tests/unit/workflows/skills/test_deployment_update_execution.py -q`. (FR-001 through FR-008)
- [X] T017 Run focused integration tests: `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q`. (SC-006)

## Final Phase: Polish and Verification

- [X] T018 Run traceability grep for `MM-521`, `DESIGN-REQ-001`, and `PARTIALLY_VERIFIED` across feature artifacts, code, and tests. (FR-009, SC-007)
- [X] T019 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification.
- [X] T020 Run `./tools/test_integration.sh` when Docker is available; otherwise record the exact blocker. Blocked: Docker socket unavailable at `unix:///var/run/docker.sock` in this managed container.
- [X] T021 Run `/moonspec-verify` for `specs/263-deployment-verification-artifacts-progress/` and produce final verification evidence.

## Dependencies and Execution Order

1. T001-T004 before story tests.
2. T005-T010 before T011-T015.
3. T011-T015 before T016-T017.
4. T016-T017 before T018-T021.

## Parallel Examples

- T005 through T009 can be drafted in parallel because they add independent assertions in one test module, but final file edits must be integrated sequentially.
- T007 and T008 validate different helper behavior and can be reasoned about independently.

## Implementation Strategy

Write failing tests for missing MM-521 semantics first, then implement the smallest executor changes needed for explicit partial status, audit metadata, recursive redaction, and bounded progress. Preserve the existing public tool name/version and current evidence completeness behavior.
