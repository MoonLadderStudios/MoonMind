# Tasks: Claude Governance Telemetry

**Input**: `specs/191-claude-governance-telemetry/spec.md`  
**Plan**: `specs/191-claude-governance-telemetry/plan.md`  
**Contracts**: `specs/191-claude-governance-telemetry/contracts/claude-governance-telemetry.md`  
**Unit command**: `pytest tests/unit/schemas/test_claude_governance_telemetry.py -q`  
**Integration command**: `pytest tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q`

## Source Traceability

The original MM-349 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-029, acceptance scenarios 1-6, edge cases, SC-001 through SC-007, and DESIGN-REQ-019 through DESIGN-REQ-025, DESIGN-REQ-028, and DESIGN-REQ-029.

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist in `specs/191-claude-governance-telemetry/spec.md`, `specs/191-claude-governance-telemetry/plan.md`, `specs/191-claude-governance-telemetry/research.md`, `specs/191-claude-governance-telemetry/data-model.md`, `specs/191-claude-governance-telemetry/contracts/claude-governance-telemetry.md`, and `specs/191-claude-governance-telemetry/quickstart.md`
- [X] T002 Confirm existing Claude schema boundary in `moonmind/schemas/managed_session_models.py` and `moonmind/schemas/__init__.py` for MM-342 through MM-348 dependencies
- [X] T003 Confirm focused test locations in `tests/unit/schemas/` and `tests/integration/schemas/`

## Phase 2: Foundational

- [X] T004 Preserve MM-349 canonical Jira preset brief in `docs/tmp/jira-orchestration-inputs/MM-349-moonspec-orchestration-input.md` and `specs/191-claude-governance-telemetry/spec.md`
- [X] T005 Review compact metadata validation helpers in `moonmind/schemas/_validation.py` and `moonmind/schemas/temporal_payload_policy.py` for payload-light guardrails (FR-003, FR-009, FR-010)
- [X] T006 Confirm existing Claude hook, policy, child-work, surface, checkpoint, and context exports in `moonmind/schemas/__init__.py` before adding governance telemetry exports (FR-020 through FR-027)

## Phase 3: Story - Claude Governance Telemetry

**Summary**: As an enterprise auditor, I want Claude managed-session events, storage references, telemetry, retention metadata, and governance evidence exported without centralizing source code by default so that sessions can be reviewed safely and consistently.

**Independent Test**: Run synthetic Claude managed-session flows that include policy decisions, hooks, checkpoints, compactions, subagents, team messages, surface reconnects, and usage, then assert event subscriptions, storage pointers, retention metadata, normalized metrics, trace span names, usage rollups, and compliance export records without embedding source code, transcripts, or checkpoint payloads in central-plane records.

**Traceability IDs**: FR-001 through FR-029; DESIGN-REQ-019 through DESIGN-REQ-025, DESIGN-REQ-028, DESIGN-REQ-029; SC-001 through SC-007

### Unit Test Plan

- Event subscription and envelope validation: FR-001 through FR-005, SC-001, DESIGN-REQ-019, DESIGN-REQ-020
- Payload-light storage and retention validation: FR-006 through FR-014, SC-002, SC-003, DESIGN-REQ-021, DESIGN-REQ-022
- Telemetry, usage, governance, compliance, and dashboard validation: FR-015 through FR-029, SC-004 through SC-007, DESIGN-REQ-023 through DESIGN-REQ-025, DESIGN-REQ-028, DESIGN-REQ-029

### Integration Test Plan

- Synthetic boundary fixture: acceptance scenarios 1-6, edge cases, SC-006, SC-007, full source design coverage

### Tests First

- [X] T007 Add failing unit tests for event subscription and event envelope validation in `tests/unit/schemas/test_claude_governance_telemetry.py` (FR-001 through FR-005, SC-001, DESIGN-REQ-019, DESIGN-REQ-020)
- [X] T008 Add failing unit tests for payload-light storage evidence and retention evidence in `tests/unit/schemas/test_claude_governance_telemetry.py` (FR-006 through FR-014, SC-002, SC-003, DESIGN-REQ-021, DESIGN-REQ-022)
- [X] T009 Add failing unit tests for telemetry evidence, usage rollup validation, governance evidence, compliance export view, and dashboard summary in `tests/unit/schemas/test_claude_governance_telemetry.py` (FR-015 through FR-029, SC-004, SC-005, DESIGN-REQ-023 through DESIGN-REQ-025, DESIGN-REQ-028, DESIGN-REQ-029)
- [X] T010 [P] Add failing integration-style boundary test for the synthetic governance telemetry fixture flow in `tests/integration/schemas/test_claude_governance_telemetry_boundary.py` (acceptance scenarios 1-6, SC-006, SC-007)
- [X] T011 Run `pytest tests/unit/schemas/test_claude_governance_telemetry.py tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q` and confirm the new tests fail before implementation

### Implementation

- [X] T012 Add Claude governance telemetry literal types, allowed-name tuples, and payload-light metadata guard helpers in `moonmind/schemas/managed_session_models.py` (FR-001 through FR-005, FR-009, FR-015 through FR-018)
- [X] T013 Add `ClaudeEventSubscription` and `ClaudeEventEnvelope` models in `moonmind/schemas/managed_session_models.py` (FR-001 through FR-005, DESIGN-REQ-019, DESIGN-REQ-020)
- [X] T014 Add `ClaudeStorageEvidence`, `ClaudeRetentionClass`, and `ClaudeRetentionEvidence` models in `moonmind/schemas/managed_session_models.py` (FR-006 through FR-014, DESIGN-REQ-021, DESIGN-REQ-022)
- [X] T015 Add `ClaudeTelemetryMetric`, `ClaudeTelemetrySpan`, and `ClaudeTelemetryEvidence` models in `moonmind/schemas/managed_session_models.py` (FR-015 through FR-018, DESIGN-REQ-023)
- [X] T016 Add `ClaudeUsageRollup`, `ClaudeGovernanceEvidence`, `ClaudeComplianceExportView`, and `ClaudeProviderDashboardSummary` models in `moonmind/schemas/managed_session_models.py` (FR-019 through FR-029, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-028, DESIGN-REQ-029)
- [X] T017 Add `ClaudeGovernanceTelemetryFixtureFlow` and `build_claude_governance_telemetry_fixture_flow` in `moonmind/schemas/managed_session_models.py` (acceptance scenarios 1-6, SC-006, SC-007)
- [X] T018 Export all governance telemetry symbols from `moonmind/schemas/__init__.py` (contracts/claude-governance-telemetry.md)

### Story Validation

- [X] T019 Run `pytest tests/unit/schemas/test_claude_governance_telemetry.py -q` and record result in this file
- [X] T020 Run `pytest tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q` and record result in this file
- [X] T021 Run related Claude schema regressions with `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/unit/schemas/test_claude_policy_envelope.py tests/unit/schemas/test_claude_context_snapshots.py tests/unit/schemas/test_claude_checkpoints.py tests/unit/schemas/test_claude_child_work.py tests/unit/schemas/test_claude_surfaces_handoff.py -q` and record result in this file

Validation evidence:
- Red-first confirmation: `pytest tests/unit/schemas/test_claude_governance_telemetry.py tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q` failed before implementation with missing governance telemetry imports.
- Focused unit: `pytest tests/unit/schemas/test_claude_governance_telemetry.py -q` passed, 7 passed.
- Focused integration-style: `pytest tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q` passed, 1 passed.
- Related Claude schema regression: `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/unit/schemas/test_claude_policy_envelope.py tests/unit/schemas/test_claude_context_snapshots.py tests/unit/schemas/test_claude_checkpoints.py tests/unit/schemas/test_claude_child_work.py tests/unit/schemas/test_claude_surfaces_handoff.py -q` passed, 131 passed.

## Final Phase: Polish And Verification

- [X] T022 Review `specs/191-claude-governance-telemetry/spec.md`, `specs/191-claude-governance-telemetry/plan.md`, and `specs/191-claude-governance-telemetry/tasks.md` for MM-349 traceability and no stale placeholders
- [X] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` when time permits and record result or exact blocker in this file
- [X] T024 Run `./tools/test_integration.sh` when Docker is available and record result or exact blocker in this file
- [X] T025 Run final MoonSpec verification for `specs/191-claude-governance-telemetry/spec.md` and record the verdict in `specs/191-claude-governance-telemetry/verification.md`

Final validation evidence:
- Placeholder review: `rg -n "NEEDS CLARIFICATION|\\[FEATURE|###-feature|ACTION REQUIRED|TODO|PLACEHOLDER" specs/191-claude-governance-telemetry docs/tmp/jira-orchestration-inputs/MM-349-moonspec-orchestration-input.md` found no stale placeholders outside the checklist statement that no clarification markers remain.
- Full unit wrapper: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed; Python reported 3424 passed, 1 xpassed, 111 warnings, 16 subtests passed; frontend Vitest reported 10 files and 224 tests passed.
- Hermetic integration wrapper: `./tools/test_integration.sh` could not run because Docker is unavailable in this managed container: `failed to connect to the docker API at unix:///var/run/docker.sock`.

## Dependencies And Execution Order

1. Setup and foundational tasks T001 through T006.
2. Tests first: T007 through T010.
3. Red-first confirmation T011.
4. Implementation T012 through T018.
5. Focused validation T019 through T021.
6. Final verification T022 through T025.

## Parallel Examples

The integration boundary test can be authored in parallel with the unit test module because it touches a different file:

```text
T010 integration boundary fixture test
```

## Implementation Strategy

Keep the implementation at the existing Claude schema boundary. Add compact Pydantic contracts and deterministic fixture helpers only; do not introduce persistence, live provider calls, dashboards, or external telemetry backends. Preserve payload-light defaults by validating that central-plane evidence stores references and summaries instead of source code, transcripts, file reads, checkpoint payloads, or local caches.
