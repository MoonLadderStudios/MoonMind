# Tasks: Remediation Authority Policy

**Input**: Design documents from `/specs/319-remediation-authority-policy/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style API/UI tests are REQUIRED. This story is verification-first because `plan.md` classifies the authority/policy behavior as already implemented and verified by existing tests.

**Organization**: Tasks are grouped around one MM-619 story: govern remediation authority through explicit modes, named principals, permissions, approval policy, and redaction rules.

**Source Traceability**: `MM-619`; FR-001 through FR-016; SCN-001 through SCN-007; SC-001 through SC-005; DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-017.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`
- UI tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the MM-619 artifact set and existing test boundaries are ready.

- [X] T001 Confirm MoonSpec artifact structure exists in `specs/319-remediation-authority-policy/` for MM-619.
- [X] T002 Confirm `specs/319-remediation-authority-policy/spec.md` preserves the canonical Jira preset brief and exactly one user story. (FR-016, SC-005)
- [X] T003 Confirm `specs/319-remediation-authority-policy/plan.md`, `research.md`, `data-model.md`, `contracts/remediation-authority-policy.md`, and `quickstart.md` exist and reference MM-619. (FR-016, SC-005)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm the existing implementation surfaces that satisfy MM-619 are present.

**Checkpoint**: Foundation ready - verification work can now begin.

- [X] T004 Confirm authority mode and action policy validation exists in `moonmind/workflows/temporal/service.py`. (FR-001, FR-002, DESIGN-REQ-013)
- [X] T005 Confirm remediation action authority models and policy evaluation exist in `moonmind/workflows/temporal/remediation_actions.py`. (FR-003 through FR-015, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-017)
- [X] T006 Confirm bounded remediation link and approval presentation exists in `api_service/api/routers/executions.py`. (FR-008, FR-013, SCN-005)
- [X] T007 Confirm Mission Control remediation creation exposes mode, authority, and action policy inputs in `frontend/src/entrypoints/task-detail.tsx`. (FR-001, FR-008, SCN-005)

---

## Phase 3: Story - Govern Remediation Authority

**Summary**: As a platform administrator, I can govern remediation authority through explicit modes, named principals, permissions, approval policy, and redaction rules so that privileged remediation never becomes implicit host, secret, or visibility bypass access.

**Independent Test**: Create remediation links in observe-only, approval-gated, and admin-auto modes, then evaluate read-only, side-effecting, high-risk, raw-access, unauthorized, and secret-bearing action requests. The story passes when only policy-authorized requests are executable, approval is required where appropriate, audit identity is recorded, and serialized outputs leak no raw secrets or unauthorized target existence.

**Traceability**: FR-001 through FR-016; SCN-001 through SCN-007; SC-001 through SC-005; DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-017.

**Test Plan**:

- Unit: authority-mode validation, observe-only side-effect denial, approval-gated decisioning, admin profile permission checks, high-risk approval handling, raw-operation denial, idempotency shape isolation, and redaction.
- Integration-style API/UI: remediation creation payload preservation, bounded approval-state serialization, and Mission Control remediation authority/action policy submission.

### Verification Tests

- [X] T008 Run focused remediation authority unit tests with `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`. (FR-001 through FR-015, SCN-001 through SCN-007, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-017)
- [X] T009 Run focused Temporal service and API tests with `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`. (FR-001, FR-002, FR-004, FR-008, FR-013, SCN-002, SCN-005)
- [X] T010 Run focused Mission Control UI test with `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`. (FR-001, FR-008, SCN-005)
- [X] T011 Run traceability check `rg -n "MM-619|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-017" specs/319-remediation-authority-policy`. (FR-016, SC-005)

### Implementation

- [X] T012 Confirm no production implementation changes are required because `plan.md` marks FR-001 through FR-015 as `implemented_verified` with existing code and tests. (FR-001 through FR-015)

**Checkpoint**: The MM-619 story is validated against existing implementation and remains traceable.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Finish validation without broadening scope.

- [X] T013 Run `./tools/test_unit.sh` for the full required unit suite, or record the exact blocker if it cannot complete. (SC-001 through SC-005)
- [X] T014 Run `/moonspec-verify` equivalent for `specs/319-remediation-authority-policy/` and write `specs/319-remediation-authority-policy/verification.md`. (FR-016, SC-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion.
- **Story (Phase 3)**: Depends on Foundational phase completion.
- **Polish (Phase 4)**: Depends on focused verification and traceability.

### Within The Story

- Existing implementation evidence is verified before claiming completion.
- No new production code is planned unless focused tests expose a gap.
- Final verification depends on completed traceability and test evidence.

### Parallel Opportunities

- T004 through T007 can be inspected in parallel.
- T008 through T010 can be run independently if the test runner supports separate invocations.

---

## Implementation Strategy

1. Confirm the new MM-619 artifact set preserves the canonical Jira preset brief.
2. Confirm existing implementation surfaces are present.
3. Run focused backend authority, service/API, and UI tests.
4. Run traceability checks for MM-619 and source design IDs.
5. Run full unit validation if feasible in the managed container.
6. Produce final MoonSpec verification evidence.

## Notes

- This task list covers exactly one story: MM-619 "Govern Remediation Authority".
- Do not broaden into future typed action registry work for MM-620.
- Do not change canonical `docs/Tasks/TaskRemediation.md`; this run records execution evidence in MoonSpec artifacts.
