# MoonSpec Verification Report

**Feature**: Claude Governance Telemetry  
**Spec**: `/work/agent_jobs/mm:37e6bdde-9b7b-4c00-9bf2-76cba2c1cc42/repo/specs/191-claude-governance-telemetry/spec.md`  
**Original Request Source**: `spec.md` Input, MM-349 Jira preset brief  
**Verdict**: ADDITIONAL_WORK_NEEDED  
**Confidence**: HIGH

## Test Results

| Suite | Command | Result | Notes |
|-------|---------|--------|-------|
| Focused unit | `pytest tests/unit/schemas/test_claude_governance_telemetry.py -q` | PASS | 7 passed |
| Focused integration-style | `pytest tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q` | PASS | 1 passed; marked `integration_ci` |
| Related Claude regressions | `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/unit/schemas/test_claude_policy_envelope.py tests/unit/schemas/test_claude_context_snapshots.py tests/unit/schemas/test_claude_checkpoints.py tests/unit/schemas/test_claude_child_work.py tests/unit/schemas/test_claude_surfaces_handoff.py -q` | PASS | 131 passed |
| Full unit wrapper | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` | PASS | Python: 3424 passed, 1 xpassed, 111 warnings, 16 subtests passed. Frontend: 10 files and 224 tests passed. |
| Hermetic integration wrapper | `./tools/test_integration.sh` | NOT RUN | Docker socket unavailable: failed to connect to `unix:///var/run/docker.sock`. |

## Requirement Coverage

| Requirement | Evidence | Status | Notes |
|-------------|----------|--------|-------|
| FR-001 through FR-005 | `ClaudeEventSubscription`, `ClaudeEventEnvelope`, event family/name constants in `moonmind/schemas/managed_session_models.py`; unit tests in `tests/unit/schemas/test_claude_governance_telemetry.py` | VERIFIED | Subscription scope, event families, normalized names, and unsupported-name failures are covered. |
| FR-006 through FR-014 | `ClaudeStorageEvidence`, `ClaudeRetentionClass`, `ClaudeRetentionEvidence`; unit tests for payload-light metadata and required retention classes | VERIFIED | Central stores, runtime-local payload classes, artifact refs, policy-controlled retention, and hard-coded fallback rejection are covered. |
| FR-015 through FR-018 | `ClaudeTelemetryMetric`, `ClaudeTelemetrySpan`, `ClaudeTelemetryEvidence`; unit tests for supported metric and span names | VERIFIED | OTel-derived metric/event/span normalization is represented with closed names. |
| FR-019 | `ClaudeUsageRollup`; unit tests for child/team dimensions and parent-rollup double-counting guards | VERIFIED | Session, group, user, workspace, runtime, provider, token direction, child, and team dimensions are covered. |
| FR-020 through FR-028 | `ClaudeGovernanceEvidence`, `ClaudeComplianceExportView`, `ClaudeProviderDashboardSummary`; unit and boundary tests | VERIFIED | Policy trust, provider mode, execution mode, protected paths, hooks, evidence refs, compliance export, and dashboard summary are covered. |
| FR-029 | `spec.md`, `tasks.md`, this verification report | VERIFIED | MM-349 is preserved in the canonical input and verification artifacts. |

## Acceptance Scenario Coverage

| Scenario | Evidence | Status | Notes |
|----------|----------|--------|-------|
| 1. Subscribe to normalized event evidence | `test_event_subscription_and_envelope_validate_closed_families`; boundary fixture includes all supported families | VERIFIED | Session, surface, policy, turn, work, decision, and child-work families are present. |
| 2. Payload-light central storage | `test_storage_evidence_is_payload_light_by_default`; boundary payload-light assertions | VERIFIED | Runtime-local payload keys are rejected in default central evidence. |
| 3. Policy-controlled retention | `test_retention_evidence_requires_policy_controlled_complete_classes`; boundary retention assertions | VERIFIED | Required classes are complete and policy-controlled. |
| 4. OTel normalization | `test_telemetry_evidence_accepts_supported_metric_and_span_names`; boundary telemetry assertions | VERIFIED | Metrics and spans use supported names. |
| 5. Usage rollups | `test_usage_rollup_rejects_double_counting_shapes`; boundary rollup assertions | VERIFIED | Child/team dimensions and total rollups are represented. |
| 6. Governance export | `test_governance_compliance_and_dashboard_evidence_are_bounded`; boundary compliance/dashboard assertions | VERIFIED | Governance record distinguishes policy trust, provider mode, protected paths, hooks, and execution mode. |

## Constitution And Source Design Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DESIGN-REQ-019 | Event envelope identities, storage refs, usage rollups, boundary fixture | VERIFIED | Session/group/event/artifact/usage identities are preserved. |
| DESIGN-REQ-020 | Event subscription and event family/name validation | VERIFIED | Subscription and normalized event family coverage is present. |
| DESIGN-REQ-021 | Payload-light storage evidence and runtime-local payload rejection | VERIFIED | Central-plane defaults avoid embedded runtime payloads. |
| DESIGN-REQ-022 | Retention evidence required-class validation | VERIFIED | Retention is policy-controlled and complete. |
| DESIGN-REQ-023 | Telemetry metric/span models and tests | VERIFIED | Claude observations map into shared metric/event/span evidence. |
| DESIGN-REQ-024 | Governance evidence model and tests | VERIFIED | Policy trust, provider mode, protected paths, hooks, and execution mode are represented. |
| DESIGN-REQ-025 | `ClaudeHookAudit` embedded in governance evidence | VERIFIED | Hook name, source scope, event type, matcher, and outcome are carried through. |
| DESIGN-REQ-028 | Compliance export and provider dashboard summary | VERIFIED | Enterprise telemetry/audit exports are derivable from bounded evidence. |
| DESIGN-REQ-029 | Usage rollup model and tests | VERIFIED | Usage dimensions cover session, group, user, workspace, runtime, provider, child, and team dimensions. |
| Constitution I-XIII | Plan gates, schema-boundary implementation, local-owned data, fail-fast unsupported values, test evidence | VERIFIED | No constitution conflicts found. |

## Original Request Alignment

- PASS for using the MM-349 Jira preset brief as canonical input.
- PASS for runtime mode: production schema contracts and tests were implemented.
- PASS for treating `docs/ManagedAgents/ClaudeCodeManagedSessions.md` as runtime source requirements rather than documentation-only work.
- PASS for inspecting existing MoonSpec artifacts and creating the next missing spec stage because no existing MM-349 feature directory existed.

## Gaps

- The required compose-backed hermetic integration wrapper was not run because Docker is unavailable in this managed container.

## Remaining Work

1. Run `./tools/test_integration.sh` in an environment with Docker socket access.

## Decision

The MM-349 implementation is complete at the code and focused validation level, with unit and integration-style boundary evidence passing. Final closeout remains blocked only on the environment-level hermetic integration wrapper.
