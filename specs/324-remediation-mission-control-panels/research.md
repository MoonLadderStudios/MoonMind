# Research: Remediation Mission Control Panels

## FR-001 / SC-001 - Remediation Entry Surfaces

Decision: Partial. Task detail supports remediation creation for eligible failed/stuck/attention targets, but failed banners, attention-required surfaces, stuck surfaces outside task detail, and provider/session problem surfaces need verification or additional UI entry points.
Evidence: `frontend/src/entrypoints/task-detail.tsx` `isRemediationEligibleTarget()` and task action rendering; `frontend/src/entrypoints/task-detail.test.tsx` remediation creation tests.
Rationale: The spec requires multiple operator surfaces; current evidence only proves task detail.
Alternatives considered: Treat all problem surfaces as task detail variants. Rejected because the Jira brief names distinct surfaces and the UI must be explicit.
Test implications: Frontend unit tests for each surfaced entry point; integration test for one end-to-end create submission path.

## FR-002 / FR-003 - Canonical Create Payload

Decision: Partial to implemented_unverified. UI sends mode, authority, action policy, pinned run, and bounded evidence policy; backend injects target workflowId and normalizes canonical `task.remediation`. Selected step scope is not yet visible in UI.
Evidence: `frontend/src/entrypoints/task-detail.tsx` create mutation; `api_service/api/routers/executions.py` `create_remediation_execution`; `moonmind/workflows/temporal/service.py` `_validate_remediation_link`; `tests/unit/workflows/temporal/test_temporal_service.py`.
Rationale: Payload foundations are strong but the operator cannot yet choose all vs selected steps from the UI.
Alternatives considered: Backend-only selected step support. Rejected because the story is operator-facing.
Test implications: Frontend payload tests plus route/service tests for canonical payload normalization.

## FR-004 / SC-002 - Target-Side Remediation Panel

Decision: Implemented_unverified. Inbound remediation link panel exists and renders status, authority, latest action, resolution, lock scope, and approval state.
Evidence: `RemediationRelationshipsPanel` in `frontend/src/entrypoints/task-detail.tsx`; task-detail tests render inbound remediation.
Rationale: The primary fields exist, but lock holder/badge semantics and degraded fetch state need stronger assertions.
Alternatives considered: Mark implemented_verified. Rejected because current tests do not prove every listed target-panel field and degraded state.
Test implications: Frontend unit tests.

## FR-005 / SC-003 - Remediation-Side Target Panel

Decision: Partial. Outbound panel exists, but selected steps, current target state, allowed actions, and explicit lock state are not fully represented in the response/UI model.
Evidence: `RemediationLinkSummaryModel` in `api_service/api/routers/executions.py`; `RemediationRelationshipsPanel` outbound rendering.
Rationale: The spec requires a richer remediation-side target panel than the current link summary carries.
Alternatives considered: Derive values from artifacts only. Rejected because operators need scan-friendly panel fields without opening raw evidence first.
Test implications: API contract tests and frontend unit tests.

## FR-006 / FR-009 / FR-013 / FR-014 - Evidence And Durable Fallbacks

Decision: Partial. Remediation artifacts are identified and shown by `remediation.*` type; context builder records degraded historical evidence and unavailable classes. UI does not yet surface all unavailable evidence classes and fallback sources.
Evidence: `RemediationEvidencePanel` in `frontend/src/entrypoints/task-detail.tsx`; `tests/unit/workflows/temporal/test_remediation_context.py` historical and unsupported live-follow cases.
Rationale: Evidence exists as artifacts and context payloads, but Mission Control needs clearer operator-visible degraded detail.
Alternatives considered: Require opening context artifact for degraded details. Rejected because the story requires Mission Control panels to make state understandable directly.
Test implications: Frontend unit tests and hermetic integration route/artifact fixture test.

## FR-007 / FR-008 / SC-004 - Live Observation State

Decision: Missing to partial. UI shows an unavailable live-follow fallback when follow mode lacks context artifact, but active live observation, sequence position, reconnect state, and epoch boundaries are not represented.
Evidence: task-detail fallback message; `RemediationContextBuilder` liveFollow payload tests.
Rationale: Existing backend context has live-follow metadata, but the task detail contract does not expose it as a first-class panel state.
Alternatives considered: Use the generic live logs panel. Rejected because remediation live follow must be labeled as observation and tied to remediation evidence.
Test implications: API/UI contract tests plus frontend unit tests for active and unavailable live observation.

## FR-010 / SC-005 - Approval Handoff

Decision: Partial. UI can approve/reject pending approval-gated remediation and render approval cards; backend route records decisions. Serializer currently emits only generic pending/not_required state unless richer fields are available elsewhere.
Evidence: `RemediationApprovalSummary`, `record_remediation_approval_decision`, `tests/unit/workflows/temporal/test_temporal_service.py`.
Rationale: The controls are present, but proposed action, preconditions, blast radius, risk, audit ref, and persisted decision details need complete backend-to-UI coverage.
Alternatives considered: Show details only in artifacts. Rejected because approval handoff must be directly reviewable before decision.
Test implications: Backend unit tests for serialized approval metadata and frontend tests for approve/reject/read-only/completed states.

## FR-011 / FR-012 - Target Validation And Pinned Runs

Decision: Implemented_unverified. Backend rejects missing, unauthorized, run-id, mismatched-run, non-run, and nested remediation targets and pins the current target run into stored parameters.
Evidence: `TemporalExecutionService._validate_remediation_link`; unit tests in `tests/unit/workflows/temporal/test_temporal_service.py`.
Rationale: Backend rules exist, but route/UI behavior for structured validation and displayed pinned run after target changes needs explicit verification.
Alternatives considered: No new code. Rejected until UI/API behavior is verified at the operator boundary.
Test implications: Backend route/service unit tests and frontend error-display test.

## FR-015 / FR-016 / FR-017 / FR-018 / SC-006 - Locks, Policy, Outcomes, And Failed Remediators

Decision: Partial. Mutation guard, lifecycle summary helpers, and link fields exist, but Mission Control only partially exposes lock owner, permitted outcome, forced-termination policy, no-op/precondition/verification outcomes, final summary, and lock-release state.
Evidence: `moonmind/workflows/temporal/remediation_actions.py`; `moonmind/workflows/temporal/remediation_context.py`; task-detail link rendering.
Rationale: Backend state exists in pieces, but MM-624 is the UI handoff story and needs coherent presentation.
Alternatives considered: Defer to final remediation artifacts only. Rejected because operators need task-detail panels to understand state without raw backend access.
Test implications: Frontend unit matrix for degraded/locked/precondition/failed-remediator states; backend unit if response serialization needs new fields.

## FR-019 / SC-007 - Traceability

Decision: Implemented_verified. The specification preserves `MM-624`, the original Jira preset brief, linked issue context, and source design mappings.
Evidence: `specs/324-remediation-mission-control-panels/spec.md`; `checklists/requirements.md`.
Rationale: Downstream artifacts must preserve the same source.
Alternatives considered: None.
Test implications: Final MoonSpec verification only.

## Test Strategy

Decision: Use focused frontend unit tests for UI states, backend unit tests for route/service serialization and validation, and hermetic integration tests only where API-to-service boundaries or generated contracts change.
Evidence: Existing `frontend/src/entrypoints/task-detail.test.tsx`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/workflows/temporal/test_remediation_context.py`, and `./tools/test_integration.sh` guidance.
Rationale: Most remaining gaps are UI/API contract behavior, not provider verification.
Alternatives considered: Full end-to-end browser automation. Rejected for this plan because existing Vitest/Testing Library coverage is the repo pattern for Mission Control screens.
Test implications: Unit first, integration for route/service contract if backend response fields change.
