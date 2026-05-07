# Tasks: Resolve Proposal Policy and Delivery Records

**Input**: Design documents from `specs/311-resolve-proposal-delivery-records/`
**Prerequisites**: `specs/311-resolve-proposal-delivery-records/plan.md`, `specs/311-resolve-proposal-delivery-records/spec.md`, `specs/311-resolve-proposal-delivery-records/research.md`, `specs/311-resolve-proposal-delivery-records/data-model.md`, `specs/311-resolve-proposal-delivery-records/contracts/proposal-delivery-contract.md`

**Tests**: Unit tests and integration/boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: Covers MM-597, FR-001 through FR-014, acceptance scenarios 1-7, edge cases, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-008 from `specs/311-resolve-proposal-delivery-records/spec.md`.

**Test Commands**:

- Unit tests: `python -m pytest tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py -q`
- Required unit suite: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh` when DB schema, repository persistence, or integration_ci coverage is added; otherwise run the new DB-backed/boundary pytest target directly before full unit suite
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when touching different files and not depending on incomplete work
- Every task names exact file paths and traceability IDs where applicable
- This task list covers exactly one story: Deterministic Proposal Delivery

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and current proposal surfaces before adding tests.

- [ ] T001 Review `specs/311-resolve-proposal-delivery-records/spec.md`, `specs/311-resolve-proposal-delivery-records/plan.md`, `specs/311-resolve-proposal-delivery-records/research.md`, `specs/311-resolve-proposal-delivery-records/data-model.md`, and `specs/311-resolve-proposal-delivery-records/contracts/proposal-delivery-contract.md` for MM-597 traceability.
- [ ] T002 Inspect current proposal service, repository, model, activity, and policy schema in `moonmind/workflows/task_proposals/service.py`, `moonmind/workflows/task_proposals/repositories.py`, `moonmind/workflows/task_proposals/models.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/tasks/task_contract.py`.

---

## Phase 2: Foundational

**Purpose**: Prepare shared test and persistence scaffolding needed before story implementation.

**CRITICAL**: No production story implementation begins until red-first unit and integration tests are written and confirmed failing in Phase 3.

- [ ] T003 [P] Add or extend proposal service test fixtures for resolved policy, dedup, provider metadata, and workflow origin cases in `tests/unit/workflows/task_proposals/test_service.py`. (FR-001, FR-002, FR-003, FR-006, FR-007, FR-011, FR-012, FR-013)
- [ ] T004 [P] Add or extend proposal activity test fixtures for project and MoonMind candidate routing in `tests/unit/workflows/temporal/test_proposal_activities.py`. (FR-004, FR-005, SC-002)
- [ ] T005 [P] Identify whether persisted delivery-record fields require a migration and reserve `api_service/migrations/versions/311_proposal_delivery_records.py` only if implementation changes database schema. (FR-006, FR-007, FR-013, DESIGN-REQ-005, DESIGN-REQ-008)

**Checkpoint**: Shared fixtures and schema decision are ready; story tests can now be authored.

---

## Phase 3: Story - Deterministic Proposal Delivery

**Summary**: Proposal submission resolves routing policy, target repositories, deduplication, origin metadata, and durable delivery records deterministically before provider-specific issue delivery.

**Independent Test**: Submit representative proposal candidates with explicit and default policy values, project and MoonMind targets, duplicate and non-duplicate delivery identities, workflow-origin metadata, and provider metadata; verify resolved decisions, delivery records, dedup behavior, and origin fields before external issue delivery.

**Traceability**: FR-001 through FR-014; acceptance scenarios 1-7; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-008.

**Unit Test Plan**: Validate policy merging, explicit-over-default behavior, allowlists/gates, project preservation, MoonMind rewrite, dedup identity, duplicate handling service behavior, delivery-record canonical fields, provider metadata separation, and snake_case origin metadata.

**Integration Test Plan**: Validate the `proposal.submit` activity boundary, service/repository persistence, duplicate update/link behavior, API serialization when fields change, and migration round-trip when schema changes.

### Unit Tests (write first)

- [ ] T006 Add failing unit tests for explicit-over-default policy resolution, destination allowlist rejection, capacity/gate rejection, and successful defaulted delivery in `tests/unit/workflows/temporal/test_proposal_activities.py`. (FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003)
- [ ] T007 Add failing unit tests for project repository preservation and MoonMind run-quality repository rewrite after category, severity, and approved tag gates in `tests/unit/workflows/temporal/test_proposal_activities.py`. (FR-004, FR-005, SC-002, DESIGN-REQ-004)
- [ ] T008 Add failing unit tests for dedup key/hash identity and local open duplicate update/link behavior in `tests/unit/workflows/task_proposals/test_service.py`. (FR-008, FR-009, FR-010, SC-003, SC-004, DESIGN-REQ-006)
- [ ] T009 Add failing unit tests for canonical delivery-record fields and provider-specific metadata separation in `tests/unit/workflows/task_proposals/test_service.py`. (FR-006, FR-007, FR-013, SC-005, DESIGN-REQ-005, DESIGN-REQ-008)
- [ ] T010 Add failing unit tests for workflow origin identity and snake_case origin metadata in `tests/unit/workflows/temporal/test_proposal_activities.py` and update the old camelCase expectation. (FR-011, FR-012, SC-006, DESIGN-REQ-007)
- [ ] T011 Add failing unit tests for proposal policy provider/delivery fields in `moonmind/workflows/tasks/task_contract.py` through `tests/unit/workflows/temporal/test_proposal_activities.py` or `tests/unit/workflows/task_proposals/test_service.py`. (FR-001, FR-003, DESIGN-REQ-002, DESIGN-REQ-003)

### Integration Tests (write first)

- [ ] T012 Add failing boundary test for `TemporalProposalActivities.proposal_submit` passing resolved decisions, snake_case origin metadata, and provider metadata to the trusted service in `tests/unit/workflows/temporal/test_proposal_activities.py`. (Acceptance scenarios 1, 2, 3, 7; FR-001, FR-005, FR-011, FR-012)
- [ ] T013 Add failing DB-backed repository/service test for local open duplicate lookup and update/link behavior in `tests/unit/workflows/task_proposals/test_service.py`. (Acceptance scenarios 4, 5; FR-009, FR-010, SC-003, SC-004)
- [ ] T014 [P] Add failing API or serialization test for delivery-record canonical fields and provider metadata in `tests/unit/api/routers/test_task_proposals.py` when implementation exposes new fields through proposal responses. (Acceptance scenario 6; FR-006, FR-007, FR-013, SC-005)
- [ ] T015 Add failing migration/schema round-trip test in `tests/unit/workflows/task_proposals/test_service.py` if `api_service/migrations/versions/311_proposal_delivery_records.py` is created for task proposal delivery-record fields. (FR-006, FR-007, FR-013, DESIGN-REQ-005, DESIGN-REQ-008)

### Red-First Confirmation

- [ ] T016 Run `python -m pytest tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py -q` and confirm T006-T013 fail for the expected missing MM-597 behavior before production changes.
- [ ] T017 Run the focused API/schema or migration test target from T014-T015, if added, and confirm it fails for the expected missing delivery-record field or serialization behavior before production changes.

### Conditional Fallbacks for Implemented-Unverified Rows

- [ ] T018 If T007 project preservation coverage fails, update project target classification in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/task_proposals/service.py`. (FR-004)
- [ ] T019 If T008 dedup identity coverage fails, update `_compute_dedup_fields()` in `moonmind/workflows/task_proposals/service.py`. (FR-008)
- [ ] T020 If MM-597 traceability checks fail, update `specs/311-resolve-proposal-delivery-records/spec.md`, `specs/311-resolve-proposal-delivery-records/plan.md`, and `specs/311-resolve-proposal-delivery-records/tasks.md`. (FR-014, SC-007)

### Implementation

- [ ] T021 Update `TaskProposalPolicy`, `EffectiveProposalPolicy`, and policy-building helpers in `moonmind/workflows/tasks/task_contract.py` to support provider/destination policy fields, explicit-over-default decisions, allowlists, capacity, severity, tag gates, and default runtime evidence. (FR-001, FR-002, FR-003, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003)
- [ ] T022 Update proposal delivery-record model fields in `moonmind/workflows/task_proposals/models.py` and add `api_service/migrations/versions/311_proposal_delivery_records.py` if persisted provider, external issue, delivery timestamp, sync timestamp, task snapshot ref, or provider metadata fields are added. (FR-006, FR-007, FR-013, DESIGN-REQ-005, DESIGN-REQ-008)
- [ ] T023 Update proposal repository operations in `moonmind/workflows/task_proposals/repositories.py` to find open records by provider/destination/dedup hash and update or annotate duplicate delivery paths idempotently. (FR-009, FR-010, DESIGN-REQ-006)
- [ ] T024 Update proposal service behavior in `moonmind/workflows/task_proposals/service.py` to compute dedup before create, apply resolved delivery policy, preserve canonical delivery fields, separate provider metadata, and avoid duplicate reviewer-facing records. (FR-006, FR-007, FR-008, FR-009, FR-010, FR-013, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008)
- [ ] T025 Update proposal submission activity in `moonmind/workflows/temporal/activity_runtime.py` to classify project vs MoonMind candidates before slot consumption, enforce gates, rewrite MoonMind repository only after gates pass, produce compact delivery decisions, and emit snake_case workflow origin metadata. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-011, FR-012, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007)
- [ ] T026 Update proposal API schemas and serialization in `moonmind/schemas/task_proposal_models.py` and `api_service/api/routers/task_proposals.py` if delivery-record or provider metadata fields become operator-visible. (FR-006, FR-007, FR-013, SC-005)
- [ ] T027 Update proposal defaults documentation/config examples in `api_service/config.template.toml` only for new non-secret delivery policy fields required by MM-597. (FR-001, FR-003, DESIGN-REQ-002)

### Story Validation

- [ ] T028 Run `python -m pytest tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py -q` and fix failures until MM-597 unit and boundary coverage passes.
- [ ] T029 Run the focused API/schema or migration tests from T014-T015, if added, and fix failures until delivery-record persistence and serialization coverage passes.
- [ ] T030 Run `./tools/test_unit.sh` for required unit-suite verification and record any blocker in `specs/311-resolve-proposal-delivery-records/quickstart.md` or final verification notes.
- [ ] T031 Run `./tools/test_integration.sh` only if an `integration_ci` DB/migration/repository test was added; otherwise record why compose-backed integration was not required in `specs/311-resolve-proposal-delivery-records/quickstart.md`. (SC-003, SC-004, SC-005)

**Checkpoint**: The single story is implemented, unit and required boundary/integration tests pass or have exact blockers, and MM-597 traceability is preserved.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed MM-597 story without adding hidden scope.

- [ ] T032 Review `moonmind/workflows/task_proposals/service.py`, `moonmind/workflows/task_proposals/repositories.py`, and `moonmind/workflows/temporal/activity_runtime.py` for duplicated policy/dedup logic and refactor only within MM-597 scope. (FR-001, FR-008, FR-010)
- [ ] T033 Review redaction of provider metadata, origin metadata, and task snapshots in `moonmind/workflows/task_proposals/service.py` and tests for secret-like values. (FR-013, DESIGN-REQ-008)
- [ ] T034 [P] Update `specs/311-resolve-proposal-delivery-records/quickstart.md` with final focused commands, integration decision, and any environment blockers observed during implementation. (SC-001 through SC-007)
- [ ] T035 Confirm `MM-597` appears in `specs/311-resolve-proposal-delivery-records/spec.md`, `specs/311-resolve-proposal-delivery-records/plan.md`, `specs/311-resolve-proposal-delivery-records/tasks.md`, implementation notes, commit text, and PR metadata before publishing. (FR-014, SC-007)
- [ ] T036 Run `/moonspec-verify` for `specs/311-resolve-proposal-delivery-records/spec.md` after implementation and tests pass, and save the verification result under `specs/311-resolve-proposal-delivery-records/verification.md`. (Final verification)

---

## Dependencies and Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 artifact and code inspection.
- Phase 3 depends on Phase 2 fixtures/schema decision.
- Production implementation tasks T021-T027 depend on red-first confirmation T016-T017.
- Story validation T028-T031 depends on implementation tasks T021-T027 and conditional fallback tasks T018-T020 where applicable.
- Phase 4 depends on story validation.

### Within the Story

- Unit tests T006-T011 must be written before implementation tasks T021-T027.
- Integration and boundary tests T012-T015 must be written before implementation tasks T021-T027.
- Red-first confirmation T016-T017 must run before production changes.
- Conditional fallback tasks T018-T020 run only if implemented-unverified behavior fails verification tests.
- Policy schema changes T021 should precede activity/service integration T024-T025.
- Model/migration changes T022 should precede repository/service persistence changes T023-T024.
- API serialization T026 depends on selected model fields from T022.

### Parallel Opportunities

- T003, T004, and T005 can run in parallel after Phase 1.
- T006 through T011 should be grouped by target file and authored serially within each file to avoid same-file conflicts.
- T012 and T014 can run in parallel because they target different files; T013 and T015 should be coordinated with other `test_service.py` edits.
- T021 and T022 can start in parallel after red-first confirmation; T023-T027 depend on their outputs.
- T034 can run in parallel with final review tasks only after story validation; T032 and T033 should be serialized because they both inspect `service.py`.

## Parallel Example

```bash
# After Phase 2, split ownership by file:
Task owner A: "T006/T007/T010/T011/T012 in tests/unit/workflows/temporal/test_proposal_activities.py"
Task owner B: "T008/T009/T013/T015 in tests/unit/workflows/task_proposals/test_service.py"
Task owner C: "T014 in tests/unit/api/routers/test_task_proposals.py if API fields are exposed"
```

## Implementation Strategy

1. Confirm active artifacts and existing proposal code paths.
2. Add fixtures and schema decision notes without production behavior changes.
3. Write unit and integration/boundary tests first.
4. Run focused tests and confirm red-first failures.
5. Implement policy schema, delivery records, repository dedup, service persistence, activity routing, and optional API/config updates.
6. Run focused tests, required unit suite, and integration suite when required.
7. Preserve MM-597 traceability and run `/moonspec-verify`.

## Requirement Status Coverage

- Code-and-test rows: FR-001, FR-002, FR-003, FR-005, FR-006, FR-007, FR-009, FR-010, FR-011, FR-012, FR-013, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008.
- Verification-only rows with conditional fallback: FR-004, FR-008, FR-014, SC-007.
- Already verified rows: none.

## Notes

- The prerequisite helper `.specify/scripts/bash/check-prerequisites.sh --json` rejected the managed branch name, so this task list uses `.specify/feature.json` and `specs/311-resolve-proposal-delivery-records/` as the active feature directory.
- Do not implement broad proposal generation, human promotion flow, or provider credential handling beyond what MM-597 requires.
- Do not mutate checked-in skill folders while implementing this story.
