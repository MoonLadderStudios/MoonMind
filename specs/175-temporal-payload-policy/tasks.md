# Tasks: Temporal Payload Policy

**Input**: `specs/175-temporal-payload-policy/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/temporal-payload-policy.md`

## Phase 1: Tests First

- [X] T001 Add failing schema tests for nested raw bytes and large text rejection in `tests/schemas/test_temporal_payload_policy.py` (FR-001, FR-002, FR-003).
- [X] T002 Add failing schema tests for compact managed-session artifact refs and integration provider-summary refs in `tests/schemas/test_temporal_payload_policy.py` (FR-004, FR-005).

## Phase 2: Implementation

- [X] T003 Add reusable compact Temporal mapping validation in `moonmind/schemas/temporal_payload_policy.py` (FR-001, FR-002, FR-003).
- [X] T004 Apply compact metadata validation to agent-runtime models in `moonmind/schemas/agent_runtime_models.py` (FR-002, FR-003).
- [X] T005 Apply compact metadata validation to managed-session models in `moonmind/schemas/managed_session_models.py` (FR-002, FR-004).
- [X] T006 Apply provider-summary validation to integration Temporal models and signals in `moonmind/schemas/temporal_models.py` and `moonmind/schemas/temporal_signal_contracts.py` (FR-002, FR-005).

## Phase 3: Validation

- [X] T007 Run focused schema tests for payload policy and existing explicit binary serialization.
- [X] T008 Run full required unit suite with `./tools/test_unit.sh`.
- [X] T009 Run final `/speckit.verify` style artifact/code alignment check.
