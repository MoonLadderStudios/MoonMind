# Tasks: Proposal Review Delivery

**Input**: Design artifacts from `/specs/312-proposal-review-delivery/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-review-delivery-contract.md`, `quickstart.md`
**Story**: Deliver Proposals To External Review Surfaces
**Jira Traceability**: MM-598

## Source Traceability

This task plan implements exactly one story from MM-598. It preserves the Jira preset brief and source-design mappings carried in `spec.md`:

- `DESIGN-REQ-001`: Proposal artifacts derived from workflow outputs and stored snapshots
- `DESIGN-REQ-014`: Reviewer links back to canonical proposal/run evidence
- `DESIGN-REQ-015`: GitHub issue delivery with review context and commands
- `DESIGN-REQ-016`: Jira issue delivery with review context and commands
- `DESIGN-REQ-027`: Stored-snapshot safety and no raw executable payload replacement from comments
- `DESIGN-REQ-031`: Allowlisted providers, credential hygiene, and sanitized provider errors

## Test Commands

- Focused unit red/green: `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/api/routers/test_task_proposals.py -q`
- Focused integration red/green: `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q`
- Final unit suite: `./tools/test_unit.sh`
- Final hermetic integration suite, if integration_ci coverage is added: `./tools/test_integration.sh`

## Phase 1: Setup

**Purpose**: Establish the single-story implementation surface and local test harness without changing behavior.

- [ ] T001 Create the proposal delivery module and unit-test skeleton in `moonmind/workflows/task_proposals/delivery.py` and `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T002 Confirm existing delivery record fields before implementation in `api_service/migrations/versions/311_proposal_delivery_records.py` and `moonmind/workflows/task_proposals/models.py`
- [ ] T003 [P] Add reusable fake GitHub, Jira, repository, and clock fixtures for proposal delivery tests in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T004 [P] Add fixture helpers for stored task snapshots, redaction probes, and reviewer command samples in `tests/unit/workflows/task_proposals/test_delivery.py`

## Phase 2: Red-First Unit Tests

**Purpose**: Write failing unit tests for the single MM-598 story before production behavior.

- [ ] T005 [P] Add failing GitHub issue rendering tests covering review context, evidence links, reviewer commands, stored-snapshot notice, and no executable payload replacement in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T006 [P] Add failing Jira ADF rendering tests covering review context, evidence links, reviewer commands, stored-snapshot notice, and no executable payload replacement in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T007 [P] Add failing delivery orchestration tests for provider selection, stored snapshot usage, external key/url persistence, and local duplicate reuse in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T008 [P] Add failing reviewer decision parsing tests for approve, reject, dismiss, unknown commands, and immutable stored payload behavior in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T009 [P] Add failing allowlist, credential redaction, provider error sanitization, and retry classification tests in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T010 Update failing proposal service tests for post-create delivery invocation, duplicate delivery suppression, and existing open external issue reuse in `tests/unit/workflows/task_proposals/test_service.py`
- [ ] T011 Update failing Temporal proposal activity tests for delivery decisions, provider metadata, sanitized failures, and task snapshot references in `tests/unit/workflows/temporal/test_proposal_activities.py`
- [ ] T012 Update failing API visibility tests for reviewer delivery state, external issue links, stored-snapshot notices, and finish-summary fields in `tests/unit/api/routers/test_task_proposals.py`

## Phase 3: Red-First Integration Tests

**Purpose**: Capture provider-boundary behavior hermetically before implementation.

- [ ] T013 Add a failing hermetic GitHub proposal delivery integration test with fake provider calls, dedup marker matching, and external issue update/create assertions in `tests/integration/temporal/test_proposal_review_delivery.py`
- [ ] T014 Add a failing hermetic Jira proposal delivery integration test with fake trusted Jira tool calls, ADF payload assertions, and sanitized error assertions in `tests/integration/temporal/test_proposal_review_delivery.py`
- [ ] T015 Add a failing reviewer decision ingestion integration test covering approve/reject/dismiss comments, immutable stored snapshots, and run evidence links in `tests/integration/temporal/test_proposal_review_delivery.py`

## Phase 4: Red Confirmation

**Purpose**: Verify the test suite fails for missing behavior before writing production code.

- [ ] T016 Run `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/api/routers/test_task_proposals.py -q` and record the expected red failures in `specs/312-proposal-review-delivery/tasks.md`
- [ ] T017 Run `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q` and record the expected red failures in `specs/312-proposal-review-delivery/tasks.md`

## Phase 5: Implementation

**Purpose**: Implement the one story after red-first evidence exists.

- [ ] T018 Define proposal delivery request/result models, provider ports, renderer interfaces, and sanitized error types in `moonmind/workflows/task_proposals/delivery.py`
- [ ] T019 Implement the GitHub issue Markdown renderer with proposal summary, source context, evidence links, commands, dedup marker, stored-snapshot notice, and redacted metadata in `moonmind/workflows/task_proposals/delivery.py`
- [ ] T020 Implement the Jira ADF renderer with proposal summary, source context, evidence links, commands, dedup marker, stored-snapshot notice, and redacted metadata in `moonmind/workflows/task_proposals/delivery.py`
- [ ] T021 Implement delivery orchestration for provider policy resolution, allowlist enforcement, local duplicate lookup, external create/update choice, retry classification, and delivery record updates in `moonmind/workflows/task_proposals/delivery.py`
- [ ] T022 Extend the GitHub adapter boundary for proposal issue create/update/search calls and sanitized provider failures in `moonmind/workflows/adapters/github_service.py`
- [ ] T023 Implement the trusted Jira delivery adapter through existing Jira tool orchestration and ADF helpers in `moonmind/workflows/task_proposals/delivery.py`
- [ ] T024 Implement reviewer decision command parsing and normalized approve/reject/dismiss outcomes without mutating stored executable payloads in `moonmind/workflows/task_proposals/delivery.py`
- [ ] T025 Wire `TaskProposalService` to invoke proposal delivery, persist external keys/urls/provider metadata, reuse open duplicates, and preserve stored task snapshots in `moonmind/workflows/task_proposals/service.py`
- [ ] T026 Wire Temporal proposal submission activities to pass effective delivery policy, provider metadata, task snapshots, and sanitized delivery decisions in `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T027 Wire API and dashboard response fields for delivery provider, status, reviewer issue link, stored-snapshot notice, and finish-summary visibility in `api_service/api/routers/task_proposals.py`
- [ ] T028 Extend task detail or run summary projection for delivered proposal review state and external links in `api_service/api/routers/task_dashboard.py`
- [ ] T029 Export delivery types needed by service and activity tests in `moonmind/workflows/task_proposals/__init__.py`
- [ ] T030 Apply conditional fallback updates if existing record fields are insufficient while avoiding new persistence unless required in `moonmind/workflows/task_proposals/models.py`
- [ ] T031 Apply conditional fallback updates if reviewer delivery state cannot be represented by existing API contracts in `moonmind/workflows/task_contract.py`

## Phase 6: Story Validation

**Purpose**: Prove the single story works independently through unit and integration coverage.

- [ ] T032 Run `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/api/routers/test_task_proposals.py -q` and fix failures in the touched implementation and test files
- [ ] T033 Run `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q` and fix failures in the touched implementation and test files
- [ ] T034 Validate the independent story criteria from `specs/312-proposal-review-delivery/spec.md` against GitHub delivery, Jira delivery, reviewer decisions, duplicate handling, and redaction behavior
- [ ] T035 Validate the contract examples from `specs/312-proposal-review-delivery/contracts/proposal-review-delivery-contract.md` against implemented response shapes and provider calls

## Phase 7: Polish And Verification

**Purpose**: Finish traceability, full-suite verification, and final Moon Spec verification.

- [ ] T036 Add edge-case coverage for missing provider credentials, disallowed provider targets, transient provider errors, stale external records, and unknown reviewer commands in `tests/unit/workflows/task_proposals/test_delivery.py`
- [ ] T037 Run `./tools/test_unit.sh` and fix failures in the touched files
- [ ] T038 Run `./tools/test_integration.sh` if `tests/integration/temporal/test_proposal_review_delivery.py` is marked `integration_ci`; otherwise run `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q`
- [ ] T039 Execute the manual quickstart validation in `specs/312-proposal-review-delivery/quickstart.md` against the implemented behavior
- [ ] T040 Verify traceability for MM-598, all FR IDs, all scenario IDs, and all source-design coverage IDs in `specs/312-proposal-review-delivery/spec.md`, `specs/312-proposal-review-delivery/plan.md`, and `specs/312-proposal-review-delivery/tasks.md`
- [ ] T041 Run `/moonspec-verify` for `specs/312-proposal-review-delivery` and address any FAIL or PARTIAL findings before publishing implementation results

## Dependencies

- Setup tasks T001-T004 must complete before red-first tests.
- Unit tests T005-T012 must complete before integration tests T013-T015.
- Red confirmation T016-T017 must complete before implementation tasks T018-T031.
- Implementation tasks T018-T031 must complete before story validation T032-T035.
- Story validation T032-T035 must complete before polish and final verification T036-T041.

## Parallel Opportunities

- T003 and T004 can run in parallel after T001.
- T005-T009 can run in parallel once the unit-test skeleton exists.
- T013-T015 can run in parallel after the unit red tests define the expected behavior.
- T019 and T020 can run in parallel after T018.
- T022 and T023 can run in parallel after T021 defines the provider port.
- T036 can run in parallel with traceability review T040 after T032-T035 pass.

## Completion Criteria

- `tasks.md` covers exactly one story: Deliver Proposals To External Review Surfaces.
- Red-first unit and integration tests are written and observed failing before implementation.
- Production work is sequenced after failing tests and includes GitHub delivery, Jira delivery, reviewer decisions, deduplication, stored-snapshot safety, dashboard/API visibility, and redaction.
- Final validation includes focused tests, full unit suite, applicable integration suite, quickstart validation, traceability review, and `/moonspec-verify`.
