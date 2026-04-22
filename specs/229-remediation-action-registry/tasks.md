# Tasks: Remediation Action Registry

**Input**: Design documents from `/specs/229-remediation-action-registry/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and service-boundary integration-style tests are REQUIRED. Tests were added first for the missing MM-454 contract gaps, confirmed red, then production code was updated until they passed.

**Organization**: Tasks are grouped by phase around the single MM-454 story.

**Source Traceability**: Tasks reference FR-001 through FR-018, SC-001 through SC-008, and DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-023, DESIGN-REQ-024.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- Runtime code: `moonmind/workflows/temporal/`
- Unit/service-boundary tests: `tests/unit/workflows/temporal/`
- MoonSpec artifacts: `specs/229-remediation-action-registry/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing project structure and active feature artifacts.

- [X] T001 Create `specs/229-remediation-action-registry/` with `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/remediation-action-registry.md`, and `tasks.md` for MM-454.
- [X] T002 Set active feature pointer in `.specify/feature.json` to `specs/229-remediation-action-registry`.
- [X] T003 [P] Confirm existing runtime module path `moonmind/workflows/temporal/remediation_actions.py` for FR-001 through FR-018.
- [X] T004 [P] Confirm existing test module path `tests/unit/workflows/temporal/test_remediation_context.py` for unit and service-boundary validation.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Verify the existing remediation link and action authority foundation used by the story.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Verify remediation link fixtures create target/remediation records for authority evaluation in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-004, FR-017).
- [X] T006 Verify `RemediationPermissionSet` and `RemediationSecurityProfile` model the caller permissions and privileged profile inputs in `moonmind/workflows/temporal/remediation_actions.py` (FR-004, FR-005, DESIGN-REQ-012).
- [X] T007 Verify the action authority service remains a decision boundary and does not execute host, Docker, SQL, storage, provider, or network operations in `moonmind/workflows/temporal/remediation_actions.py` (FR-001, FR-015, FR-016, DESIGN-REQ-024).

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Request Typed Remediation Actions

**Summary**: As a remediation task, I want to request only typed, allowlisted administrative actions and receive durable request/result artifacts so that remediation can act through audited MoonMind-owned capabilities instead of raw access.

**Independent Test**: Create a linked remediation execution, evaluate allowed, approval-gated, high-risk, unsupported, raw-access, duplicate, and redaction-sensitive action requests, then verify decisions, executable flags, risk handling, idempotency behavior, and audit payloads.

**Traceability**: FR-001 through FR-018, SC-001 through SC-008, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-023, DESIGN-REQ-024

**Test Plan**:

- Unit: action metadata filtering, authority decisions, idempotency, status mapping, redaction helpers, fail-closed behavior.
- Integration: async DB-backed service-boundary flow from remediation link/context preparation to action authority evaluation.

### Unit Tests (write first)

- [X] T008 [P] Add failing unit test for policy-compatible action listing in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-002, SC-001, DESIGN-REQ-012).
- [X] T009 [P] Add failing unit assertions for v1 request/result/audit serialization in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-008 through FR-013, SC-005, DESIGN-REQ-013).
- [X] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py -k 'lists_policy_compatible_actions or enforces_profile_permissions_and_risk'` and confirm T008-T009 fail for missing `list_allowed_actions` and missing `schemaVersion`.

### Integration Tests (write first)

- [X] T011 [P] Preserve existing async DB-backed action authority tests for observe-only, approval-gated, admin-auto, high-risk, unsupported mode, idempotency, redaction, raw access, and prepared action context in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-004 through FR-017, SC-002 through SC-007).
- [X] T012 Run the focused service-boundary test command and confirm the newly added contract tests fail before implementation in `tests/unit/workflows/temporal/test_remediation_context.py` (DESIGN-REQ-012, DESIGN-REQ-013).

### Implementation

- [X] T013 Add enabled action catalog metadata for risk, target type, input metadata, and verification hint in `moonmind/workflows/temporal/remediation_actions.py` (FR-002, FR-013, DESIGN-REQ-012).
- [X] T014 Implement `RemediationActionAuthorityService.list_allowed_actions()` in `moonmind/workflows/temporal/remediation_actions.py` (FR-002, FR-014, SC-001).
- [X] T015 Add v1 request/result serialization to `RemediationActionAuthorityResult.to_dict()` in `moonmind/workflows/temporal/remediation_actions.py` (FR-008 through FR-013, DESIGN-REQ-013).
- [X] T016 Add result status, target type, and verification hint helpers in `moonmind/workflows/temporal/remediation_actions.py` (FR-009, FR-010, FR-013).
- [X] T017 Preserve raw access denial, missing-link fail-closed behavior, idempotency cache keys, and redaction behavior in `moonmind/workflows/temporal/remediation_actions.py` (FR-007, FR-012, FR-016, FR-017, DESIGN-REQ-023, DESIGN-REQ-024).
- [X] T018 Rerun the focused red/green test command and verify it passes (SC-001, SC-005).

**Checkpoint**: The story is implemented at the remediation action authority boundary and covered by unit/service-boundary tests.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validate the completed story without broadening scope.

- [X] T019 [P] Align `specs/229-remediation-action-registry/spec.md` with the MM-454 Jira brief and source design mappings (SC-008).
- [X] T020 [P] Align `specs/229-remediation-action-registry/plan.md`, `research.md`, `data-model.md`, `contracts/remediation-action-registry.md`, and `quickstart.md` with implementation evidence (SC-008).
- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and record result (FR-001 through FR-018).
- [X] T022 Run `/moonspec-verify`-style verification by inspecting spec, plan, tasks, code, and tests for MM-454 completion.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on Story completion and passing focused tests.

### Within The Story

- T008-T009 must be written before T013-T016.
- T010 must confirm the expected red state before production edits.
- T013-T016 must complete before T018.
- T021-T022 run after implementation and artifact alignment.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T008 and T009 can run in parallel.
- T019 and T020 can run in parallel.

---

## Parallel Example: Story Phase

```bash
Task: "Add failing unit test for policy-compatible action listing in tests/unit/workflows/temporal/test_remediation_context.py"
Task: "Add failing unit assertions for v1 request/result/audit serialization in tests/unit/workflows/temporal/test_remediation_context.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Create the missing MM-454 MoonSpec artifacts.
2. Confirm the existing remediation action boundary and tests.
3. Add failing tests for the missing list and durable payload contracts.
4. Implement only the missing service methods/serialization fields.
5. Rerun focused tests, then the full remediation context test file.
6. Complete final `/speckit.verify`-style verification against MM-454.

---

## Notes

- The story intentionally does not add raw action execution.
- The service remains an authority and contract boundary; owning control planes execute supported actions after a typed request is accepted.
- Unavailable future recommended actions are omitted rather than exposed through raw access fallback.
