# Tasks: Record Audit Events and Failure Diagnostics for Skills On Demand

**Input**: Design documents from `specs/319-audit-failure-diagnostics/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/skills-on-demand-audit-diagnostics-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover one independently testable story: bounded Skills On Demand audit and diagnostics for `MM-616`.

**Source Traceability**: `MM-616`; original Jira coverage IDs DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-014 preserved in `spec.md` input; local MoonSpec mappings DESIGN-REQ-001 through DESIGN-REQ-007; FR-001 through FR-018; acceptance scenarios 1 through 5; edge cases; SC-001 through SC-007.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`
- Integration tests: `pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -m "integration_ci" -q --tb=short`
- Final unit suite: `./tools/test_unit.sh`
- Final integration suite: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and test targets before TDD work starts.

- [X] T001 Confirm `specs/319-audit-failure-diagnostics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/skills-on-demand-audit-diagnostics-contract.md`, and `quickstart.md` are present and still preserve `MM-616`, FR-001 through FR-018, and DESIGN-REQ-001 through DESIGN-REQ-007.
- [X] T002 [P] Confirm no dependency, migration, or persistent-storage setup is required for this story by reviewing `specs/319-audit-failure-diagnostics/plan.md` and `moonmind/services/skills_on_demand.py`.
- [X] T003 [P] Confirm the focused unit and integration test files exist at `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`, `tests/integration/temporal/test_skills_on_demand_disabled.py`, and `tests/integration/temporal/test_skills_on_demand_request_activation.py`.

---

## Phase 2: Foundational

**Purpose**: Establish the test harness expectations that block story work.

**CRITICAL**: No production implementation work can begin until the red-first tests and failure confirmations in Phase 3 are complete.

- [X] T004 Define the audit/diagnostic test fixture strategy in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` for capturing emitted Skills On Demand events without adding production code yet. (FR-007, FR-010, SC-001, SC-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T005 Define the activity-boundary audit capture strategy in `tests/integration/temporal/test_skills_on_demand_request_activation.py` for request activation, materialization failure, and runtime refresh failure paths without adding production code yet. (FR-002, FR-005, FR-010, FR-014, FR-016, SC-002, SC-003, DESIGN-REQ-001, DESIGN-REQ-004)

**Checkpoint**: Test harness ready; story tests can now be written and confirmed red.

---

## Phase 3: Story - Bounded Skills On Demand Audit and Diagnostics

**Summary**: As an operator, I want every Skills On Demand query and request to leave bounded audit evidence and actionable diagnostics so I can understand approvals, denials, snapshot transitions, and failures without exposing secrets or high-cardinality raw text.

**Independent Test**: Exercise Skills On Demand query and request flows for successful results, disabled feature, invalid or missing snapshot, unavailable or denied Skills, materialization failure, and runtime refresh failure; verify each flow records the expected bounded audit event, returns stable diagnostic codes where applicable, preserves active snapshots on failure, and exposes no secrets, Skill bodies, raw long query text, or arbitrary artifact/database access.

**Traceability**: FR-001 through FR-018; acceptance scenarios 1 through 5; SC-001 through SC-007; local MoonSpec DESIGN-REQ-001 through DESIGN-REQ-007; original Jira coverage IDs DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-014.

**Test Plan**:

- Unit: event and diagnostic model validation, query hash behavior, result-code normalization, redaction/bounds checks, request/query service event construction.
- Integration: Temporal activity invocation shape for disabled, denied, no-change, activated, materialization failure, runtime refresh failure, and repo projection guardrails.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough production code to make them pass.

- [X] T006 Add failing unit tests for `SkillsOnDemandFailureDiagnostic` and optional diagnostics refs in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`. (FR-003, FR-004, FR-005, FR-006, SC-003, DESIGN-REQ-002)
- [X] T007 Add failing unit tests for `skills_on_demand.query` event construction, one-event-per-query behavior, query hash use, and raw query omission in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`. (FR-007, FR-008, FR-009, SC-001, SC-004, DESIGN-REQ-003, DESIGN-REQ-005)
- [X] T008 Add failing unit tests for `skills_on_demand.request` event construction, result values including reserved `requires_approval`, requested Skill normalization, result codes, and snapshot/manifest/diagnostics refs in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`. (FR-010, FR-011, FR-012, SC-002, DESIGN-REQ-004)
- [X] T009 Add failing unit tests proving audit and diagnostic payloads omit secrets, full Skill bodies, hidden source paths, raw long query text, arbitrary artifact/database access, and repo projection mutation details in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`. (FR-013, FR-014, FR-015, FR-016, SC-005, DESIGN-REQ-006)
- [X] T010 Add failing unit tests for the full MM-616 behavior matrix in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`: disabled feature, bounded query metadata, already-active request, allowed request, policy denial, materialization failure, and runtime refresh failure. (FR-017, SC-006, DESIGN-REQ-007)
- [X] T011 Run `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` and confirm T006-T010 fail for missing audit event and diagnostics behavior, not unrelated errors.

### Integration Tests (write first)

- [X] T012 Add failing integration_ci tests for disabled query/request audit evidence and snapshot preservation in `tests/integration/temporal/test_skills_on_demand_disabled.py`. (FR-001, FR-003, FR-007, FR-010, SC-001, SC-002, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T013 Add failing integration_ci tests for allowed request activation emitting one compact `skills_on_demand.request` event with parent snapshot, derived snapshot, manifest, materialization, requested Skills, and no Skill bodies in `tests/integration/temporal/test_skills_on_demand_request_activation.py`. (FR-010, FR-011, FR-013, SC-002, SC-005, DESIGN-REQ-004, DESIGN-REQ-006)
- [X] T014 Add failing integration_ci tests for materialization failure and runtime refresh failure diagnostics refs/events in `tests/integration/temporal/test_skills_on_demand_request_activation.py`. (FR-002, FR-005, FR-006, FR-014, SC-003, DESIGN-REQ-001, DESIGN-REQ-002)
- [X] T015 Add failing integration_ci tests proving repo-authored `.agents/skills` projection changes are not reported as repo-authored source mutations in audit or diagnostics evidence in `tests/integration/temporal/test_skills_on_demand_request_activation.py`. (FR-016, SC-005, DESIGN-REQ-006)
- [X] T016 Run `pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -m "integration_ci" -q --tb=short` and confirm T012-T015 fail for missing audit event and diagnostics behavior, not unrelated errors.

### Red-First Confirmation

- [X] T017 Record the expected red-first unit and integration failures from T011 and T016 in `specs/319-audit-failure-diagnostics/tasks.md` or implementation notes before production code changes begin. (FR-017, SC-006)
- [X] T018 Confirm `moonmind/schemas/agent_skill_models.py`, `moonmind/services/skills_on_demand.py`, and `moonmind/workflows/agent_skills/agent_skills_activities.py` have not been modified before T006-T017 are complete. (FR-017, DESIGN-REQ-007)

### Conditional Fallback Implementation for Verification-First Rows

- [X] T019 If T006 or T008 reveals that existing result code/message behavior is insufficient, update `moonmind/schemas/agent_skill_models.py` and `moonmind/services/skills_on_demand.py` to provide explicit failure diagnostic structures and safe current snapshot refs. (FR-003, FR-004, FR-006, DESIGN-REQ-002)
- [X] T020 If T009, T013, or T015 reveals existing redaction and projection guardrails are insufficient, update `moonmind/services/skills_on_demand.py` and `moonmind/workflows/agent_skills/agent_skills_activities.py` to keep audit/diagnostics payloads bounded and non-secret. (FR-013, FR-015, FR-016, SC-005, DESIGN-REQ-006)

### Implementation

- [X] T021 Add Skills On Demand audit event, failure diagnostic, and diagnostic artifact models or typed helpers in `moonmind/schemas/agent_skill_models.py`. (FR-005, FR-007, FR-010, FR-012, FR-014, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T022 Implement query audit event construction and hash-only query evidence in `moonmind/services/skills_on_demand.py`. (FR-007, FR-008, FR-009, SC-001, SC-004, DESIGN-REQ-003, DESIGN-REQ-005)
- [X] T023 Implement request audit event construction, normalized requested Skill evidence, reserved `requires_approval` result support where appropriate, and compact refs in `moonmind/services/skills_on_demand.py`. (FR-010, FR-011, FR-012, SC-002, DESIGN-REQ-004)
- [X] T024 Implement controlled diagnostics ref handling for denied request, materialization failure, runtime refresh failure, and oversized diagnostic evidence in `moonmind/services/skills_on_demand.py`. (FR-003, FR-005, FR-006, FR-014, SC-003, DESIGN-REQ-002)
- [X] T025 Wire Temporal activity context into query/request audit emission in `moonmind/workflows/agent_skills/agent_skills_activities.py` without embedding Skill bodies or large diagnostics in workflow history. (FR-009, FR-010, FR-011, FR-013, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006)
- [X] T026 Preserve existing active snapshot and runtime projection behavior while attaching audit/diagnostic evidence in `moonmind/workflows/agent_skills/agent_skills_activities.py`. (FR-001, FR-002, FR-016, DESIGN-REQ-001, DESIGN-REQ-006)
- [X] T027 Run `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` and fix failures in `moonmind/schemas/agent_skill_models.py`, `moonmind/services/skills_on_demand.py`, or `moonmind/workflows/agent_skills/agent_skills_activities.py` until focused unit tests pass.
- [X] T028 Run `pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -m "integration_ci" -q --tb=short` and fix failures in `moonmind/workflows/agent_skills/agent_skills_activities.py` or related models/services until focused integration tests pass.

### Story Validation

- [X] T029 Validate the single story by reviewing `specs/319-audit-failure-diagnostics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/skills-on-demand-audit-diagnostics-contract.md`, and this `tasks.md` against implemented code and focused test evidence for FR-001 through FR-018, acceptance scenarios 1 through 5, SC-001 through SC-007, local DESIGN-REQ-001 through DESIGN-REQ-007, and original Jira coverage IDs DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-014.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T030 [P] Review `specs/319-audit-failure-diagnostics/contracts/skills-on-demand-audit-diagnostics-contract.md` and `data-model.md` against final code behavior and update only if implementation changed the documented event/diagnostic contract. (FR-018, SC-007)
- [X] T031 [P] Review `specs/319-audit-failure-diagnostics/quickstart.md` and ensure focused and final commands match the implemented test layout. (FR-017, SC-006)
- [X] T032 Run `./tools/test_unit.sh` for the full unit suite via `tools/test_unit.sh` and fix regressions within the MM-616 scope.
- [X] T033 Run `./tools/test_integration.sh` for the required hermetic integration_ci suite via `tools/test_integration.sh` or record the exact local blocker if the environment cannot run it.
- [ ] T034 Run `/moonspec-verify` after implementation and tests pass, validating against `specs/319-audit-failure-diagnostics/spec.md`, `plan.md`, `tasks.md`, preserved `MM-616` Jira preset brief, original Jira coverage IDs DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-014, local DESIGN-REQ-001 through DESIGN-REQ-007, and unit/integration evidence. (FR-018, SC-007)

---

## Dependencies and Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish and Verification (Phase 4)**: Depends on story implementation and focused tests passing.

### Within The Story

- T006-T010 unit tests must be written before implementation.
- T012-T015 integration tests must be written before implementation.
- T011 and T016 must confirm red-first failures before T019-T026.
- T019-T020 are conditional fallback implementation tasks for implemented_unverified rows and run only if verification tests expose gaps.
- T021-T026 implement missing and partial behavior after red-first confirmation.
- T027-T028 must pass before T029 story validation.
- T032-T034 are final checks after focused validation.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T006-T010 should run sequentially because they edit the same unit test file.
- T012 can run separately from T013-T015 because it edits `test_skills_on_demand_disabled.py`; T013-T015 should run sequentially because they edit the same activation test file.
- T030 and T031 can run in parallel after implementation because they touch different spec artifacts.

---

## Parallel Example

```bash
# After Phase 2, split test authoring by file:
Task: "T012 Add disabled-path integration tests in tests/integration/temporal/test_skills_on_demand_disabled.py"
Task: "T013-T015 Add activation/failure/projection tests in tests/integration/temporal/test_skills_on_demand_request_activation.py"

# After implementation and focused tests pass:
Task: "T030 Review contract/data-model docs"
Task: "T031 Review quickstart commands"
```

---

## Implementation Strategy

1. Confirm artifacts and test targets.
2. Add test harness helpers without production changes.
3. Write unit tests for diagnostics, query events, request events, redaction, and matrix coverage.
4. Write integration tests for disabled, activated, materialization failure, runtime refresh failure, and projection guardrails.
5. Confirm red-first failures.
6. Implement schemas/helpers, service event construction, diagnostics refs, and Temporal activity context wiring.
7. Run focused unit and integration tests until green.
8. Validate traceability against the preserved `MM-616` brief.
9. Run full unit and integration suites.
10. Run `/moonspec-verify`.

## Notes

- This task list covers one story only: bounded Skills On Demand audit and diagnostics for `MM-616`.
- Existing snapshot preservation and materialization behavior should be preserved, not reworked.
- Do not introduce new persistent tables unless implementation proves existing result metadata, structured logging, or artifact-backed diagnostics cannot satisfy the contract; if that happens, update `plan.md` before implementation continues.
- Keep large Skill content, raw long query text, and secret-like values out of workflow history, audit events, diagnostics, and test output.

## Implementation Evidence

- Red-first unit evidence: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` initially failed during collection because `SkillsOnDemandFailureDiagnostic` was missing.
- Red-first integration evidence: `pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -m "integration_ci" -q --tb=short` initially failed because `SkillsOnDemandRequestResult` had no `audit_events` or `failure_diagnostic`.
- Focused unit result after implementation: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` passed, including frontend test phase triggered by the unit runner.
- Focused integration result after implementation: `pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -m "integration_ci" -q --tb=short` passed with 7 tests.
- Full unit result: `./tools/test_unit.sh` passed with 4526 Python tests, 1 xpassed, 16 subtests, and 20 frontend test files.
- Full integration result: `./tools/test_integration.sh` was attempted but blocked by Docker administrative policy while compose tried to build the pytest image: `403 Forbidden`.
