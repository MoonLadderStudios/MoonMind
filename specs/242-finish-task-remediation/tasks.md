# Tasks: Finish Task Remediation Desired-State Implementation

**Input**: `specs/242-finish-task-remediation/spec.md`, `specs/242-finish-task-remediation/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/remediation-runtime.md`, `quickstart.md`

## Validation Commands

- Unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Router unit when API read models change: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
- UI when Mission Control changes: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Final unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration: `./tools/test_integration.sh` if artifact lifecycle/API routes change

## Source Traceability Summary

- MM-483 is preserved in the Jira orchestration input, spec, plan, tasks, and final verification path.
- Input classification: single-story runtime feature request.
- First implementation focus from `plan.md`: canonical action registry coverage and metadata shape.

## Phase 1: Setup

- [X] T001 Confirm active feature locator points to `specs/242-finish-task-remediation` in `.specify/feature.json`
- [X] T002 Confirm no existing MM-483 MoonSpec feature directory was present before creating `specs/242-finish-task-remediation`

## Phase 2: Foundational

- [X] T003 Review `moonmind/workflows/temporal/remediation_actions.py` for canonical registry gaps (FR-001 through FR-003)
- [X] T004 Review `tests/unit/workflows/temporal/test_remediation_context.py` for existing authority and mutation guard coverage (FR-001 through FR-015)

## Phase 3: Story - Complete Task Remediation Runtime

**Summary**: Operators can use Task Remediation through canonical typed actions, safe control-plane boundaries, durable coordination, lifecycle evidence, target summaries, and bounded self-healing policy.

**Independent Test**: Exercise remediation action metadata, authority decisions, mutation guards, lifecycle artifacts, target summaries, cancellation/continuation behavior, and Mission Control rendering without raw host/storage/secret access.

**Traceability IDs**: FR-001 through FR-038, SC-001 through SC-010, DESIGN-REQ-001 through DESIGN-REQ-006

### Unit Tests

- [X] T005 Add failing unit coverage for the complete canonical action registry in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-001, FR-003, SC-001, DESIGN-REQ-004)
- [X] T006 Add failing unit coverage proving legacy action aliases are not accepted as compatibility shims in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-038, DESIGN-REQ-004)
- [X] T007 Add failing unit coverage for `taskRunIds` ownership validation in `moonmind/workflows/temporal/service.py` through `tests/unit/workflows/temporal/test_service.py` or adjacent tests (FR-008, FR-009)
- [ ] T008 Add failing restart-durability coverage for mutation locks and action ledgers in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-012 through FR-015)
- [ ] T009 Add verification coverage for automatic runtime publication of remediation action and verification artifacts in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-005 through FR-007, FR-021 through FR-023)
- [ ] T010 Add verification coverage for bounded cancellation/failure/Continue-As-New outcomes in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-028 through FR-033)

### Implementation

- [X] T011 Replace legacy remediation action catalog entries with canonical dotted action kinds and full metadata in `moonmind/workflows/temporal/remediation_actions.py` (FR-001 through FR-003)
- [X] T012 Update authority/listing behavior to expose canonical metadata without raw execution or legacy compatibility aliases in `moonmind/workflows/temporal/remediation_actions.py` (FR-002, FR-004, FR-038)
- [X] T013 Implement `taskRunIds` ownership validation against target step/task-run evidence in `moonmind/workflows/temporal/service.py` (FR-008, FR-009)
- [ ] T014 Move mutation lock/ledger state to durable records or an explicitly persisted existing boundary in `moonmind/workflows/temporal/remediation_actions.py` and `api_service/db/models.py` (FR-012 through FR-015)
- [ ] T015 Wire action execution through owning control-plane or subsystem adapters in `moonmind/workflows/temporal/remediation_tools.py` or adjacent runtime service boundaries (FR-004 through FR-007)
- [ ] T016 Complete aggregate verification for lifecycle artifacts, target-side summaries, self-healing policy, Mission Control rendering, and bounded degraded outcomes (FR-010 through FR-036)

### Story Validation

- [X] T017 Run focused remediation tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- [ ] T018 Run final unit suite after all implementation tasks complete: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T019 Run `/moonspec-verify` and record the verdict in `specs/242-finish-task-remediation/verification.md`

## Dependencies And Order

- T005 and T006 must be written before T011 and T012.
- T011 and T012 are the first implementation slice and unblock canonical action semantics.
- T013 through T016 require additional service/API/UI boundary work after registry coverage lands.

## Implementation Strategy

Complete the story in vertical runtime slices. Start with canonical action registry semantics because every later action execution, audit, lock, and Mission Control surface depends on stable action kinds and metadata.
