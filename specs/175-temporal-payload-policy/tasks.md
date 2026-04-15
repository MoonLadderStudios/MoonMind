# Tasks: Temporal Payload Policy

**Input**: `specs/175-temporal-payload-policy/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/temporal-payload-policy.md`

**Story**: User Story 1 - Compact Temporal Payloads
**Independent Test**: Validate representative Temporal boundary models with nested raw bytes, overlarge metadata/provider summaries, explicit base64 bytes, and compact artifact refs. Raw bytes and large bodies must be rejected, while compact refs serialize as JSON.
**Traceability**: MM-330 on TOOL board; FR-001, FR-002, FR-003, FR-004, FR-005; DESIGN-REQ-017, DESIGN-REQ-019.
**Unit Test Command**: `pytest tests/schemas/test_temporal_payload_policy.py tests/schemas/test_temporal_activity_models.py -q`; final unit wrapper `./tools/test_unit.sh`.
**Integration Test Strategy**: No new compose-backed integration fixture is required while implementation remains limited to schema-boundary validation. If implementation changes workflow/activity invocation wiring, run `./tools/test_integration.sh` and add hermetic integration coverage before implementation.

## Phase 1: Setup And Planning

- [X] T001 Confirm required design artifacts exist in `specs/175-temporal-payload-policy/plan.md`, `specs/175-temporal-payload-policy/research.md`, `specs/175-temporal-payload-policy/data-model.md`, `specs/175-temporal-payload-policy/contracts/temporal-payload-policy.md`, and `specs/175-temporal-payload-policy/quickstart.md` (MM-330, DESIGN-REQ-017, DESIGN-REQ-019).
- [X] T002 Record explicit unit and integration test strategies in `specs/175-temporal-payload-policy/plan.md`, `specs/175-temporal-payload-policy/research.md`, and `specs/175-temporal-payload-policy/quickstart.md` (SC-004).

## Phase 2: Story - Compact Temporal Payloads

- [X] T003 Add failing unit/schema tests for nested raw bytes and large text rejection in `tests/schemas/test_temporal_payload_policy.py` (FR-001, FR-002, FR-003, SC-001, SC-002).
- [X] T004 Add failing unit/schema tests for compact managed-session artifact refs and integration provider-summary refs in `tests/schemas/test_temporal_payload_policy.py` (FR-004, FR-005, SC-002).
- [X] T005 Add failing explicit binary serializer regression tests in `tests/schemas/test_temporal_activity_models.py` (FR-001, SC-003).
- [X] T006 Confirm red-first failure for payload-policy tests in `tests/schemas/test_temporal_payload_policy.py` and `tests/schemas/test_temporal_activity_models.py` before production implementation (SC-001, SC-002, SC-003).
- [X] T007 Add reusable compact Temporal mapping validation in `moonmind/schemas/temporal_payload_policy.py` (FR-001, FR-002, FR-003).
- [X] T008 Apply compact metadata validation to agent-runtime models in `moonmind/schemas/agent_runtime_models.py` (FR-002, FR-003, FR-004).
- [X] T009 Apply compact metadata validation to managed-session models in `moonmind/schemas/managed_session_models.py` (FR-002, FR-004).
- [X] T010 Apply provider-summary validation to integration Temporal models and signals in `moonmind/schemas/temporal_models.py` and `moonmind/schemas/temporal_signal_contracts.py` (FR-002, FR-005).
- [X] T011 Story validation: run focused schema and explicit binary serialization tests for `tests/schemas/test_temporal_payload_policy.py` and `tests/schemas/test_temporal_activity_models.py` (SC-001, SC-002, SC-003, SC-004).

## Final Phase: Verification

- [X] T012 Run the full required unit suite with `./tools/test_unit.sh` (SC-004).
- [X] T013 Confirm integration coverage remains not required for this schema-only story, or run `./tools/test_integration.sh` if workflow/activity invocation wiring changed, and record the result in `specs/175-temporal-payload-policy/quickstart.md` (DESIGN-REQ-017, DESIGN-REQ-019).
- [X] T014 Run final `/moonspec-verify` artifact/code alignment against `specs/175-temporal-payload-policy/spec.md` after implementation and tests pass (MM-330, FR-001, FR-002, FR-003, FR-004, FR-005).
