# Research: Operations Controls Exposed as Authorized Commands

## FR-001 / DESIGN-REQ-002 Operations Command Surface

Decision: Implemented and verified; worker-pause now has a backend command route and service.
Evidence: `frontend/src/components/settings/OperationsSettingsSection.tsx` renders Operations, worker controls, and deployment controls. `api_service/api/routers/system_operations.py` implements `/api/system/worker-pause`. `api_service/services/system_operations.py` provides the command service. `tests/integration/temporal/test_system_operations_api.py` verifies the configured route shape.
Rationale: Settings remains the discovery surface while the backend route provides the runtime command boundary.
Alternatives considered: Reusing deployment operation endpoints was rejected because worker pause is a separate operational subsystem and should not be modeled as deployment update.
Test implications: Unit API, unit UI, and an integration route test.

## FR-002 / SC-001 Operations State And Feedback

Decision: Implemented and verified; backend response and UI tests cover operation metadata available from the route.
Evidence: The UI displays worker state, mode, reason, updated time, queued/running/stale/drained metrics, and recent actions. `SystemOperationsService.snapshot()` returns system, metrics, audit, and `signalStatus`. Unit UI and API tests cover the response contract.
Rationale: Existing schemas were close to the desired response shape and are now backed by route-level actor/audit/status evidence.
Alternatives considered: Creating a separate dashboard-only response shape was rejected because `WorkerPauseSnapshotResponse` is already referenced by the configured endpoint.
Test implications: Unit UI assertions and API schema tests.

## FR-003 / SC-002 Confirmation Requirements

Decision: Implemented and verified; backend confirmation enforcement exists for disruptive worker-pause commands.
Evidence: UI requires a reason for pause/resume and submits confirmation metadata. `SystemOperationsService` validates confirmation for pause and forced resume commands. Unit API/service tests cover missing confirmation.
Rationale: Backend enforcement is required because hidden or manipulated UI is not a security boundary.
Alternatives considered: Client-only confirmation was rejected by DESIGN-REQ-014 and FR-005.
Test implications: Unit API tests for missing confirmation and unit UI test for submitted confirmation fields.

## FR-004 / SC-004 Operation Command Metadata And Audit

Decision: Implemented and verified; a typed operation command model persists compact audit events using `SettingsAuditEvent`.
Evidence: `api_service/services/system_operations.py` defines `WorkerOperationCommand` and writes non-secret worker operation events to `SettingsAuditEvent`. Unit service/API tests cover persisted audit metadata and sanitized latest action response.
Rationale: The story needs auditable actions but does not justify a new table. Existing settings audit events store non-secret operation metadata without schema migration.
Alternatives considered: In-memory audit was rejected because the source design asks for audit trail. A new table was rejected because the existing table is sufficient for compact operation events.
Test implications: Unit service/API tests for persisted audit and sanitized latest action response.

## FR-005 / FR-006 / SC-003 Authorization

Decision: Implemented and verified; worker-pause operations mirror the deployment operations authorization pattern.
Evidence: `api_service/api/routers/system_operations.py` rejects non-superusers when auth is enabled, while allowing disabled-auth local development. Unit API tests cover non-admin rejection and no subsystem invocation.
Rationale: This satisfies backend authorization without inventing a larger RBAC model in this story.
Alternatives considered: Frontend-only control hiding was rejected. A new permissions table was rejected as out of scope.
Test implications: Unit API test for non-admin 403 and no subsystem invocation.

## FR-007 / SC-005 Operational Subsystem Ownership

Decision: Implemented and verified; worker quiesce/resume routes through Temporal service methods while drain mode records the system block without broadcasting pause signals.
Evidence: `moonmind/workflows/temporal/service.py` exposes quiesce pause and resume signal methods. `SystemOperationsService` delegates quiesce/resume to the injected Temporal execution service, and service/API tests assert the calls.
Rationale: Temporal remains the subsystem boundary for workflow pause/resume signaling. Settings only invokes a typed command route.
Alternatives considered: Direct workflow manipulation from the React component was rejected because Settings must not own operational semantics.
Test implications: Unit API tests assert the Temporal service methods are called for quiesce/resume and not called for unauthorized requests.

## FR-008 Operation Result Statuses

Decision: Implemented and verified; the worker-pause route returns normalized command statuses and sanitized validation/unavailable responses.
Evidence: `api_service/api/routers/system_operations.py` maps validation failures to 422, authorization failures to 403, and subsystem unavailability to 503. `SystemOperationsService.snapshot()` returns `signalStatus` in the response.
Rationale: Operator-visible results distinguish success, failure, unauthorized, conflict, and unavailable outcomes enough for the UI and tests.
Alternatives considered: Returning raw exceptions was rejected because diagnostics must be sanitized.
Test implications: Unit API tests for success and service failure.

## FR-009 Sanitized Audit

Decision: Implemented and verified; only non-secret operation metadata is stored and latest audit events are sanitized.
Evidence: `SystemOperationsService` omits confirmation secrets from persisted audit metadata and sanitizes latest audit projection. Unit service tests assert secret-like fields are not exposed.
Rationale: Operations audit must be useful without exposing secrets.
Alternatives considered: Logging full command payloads was rejected by security guardrails.
Test implications: Unit service test checks audit metadata fields and absence of secret-like raw payloads.

## FR-010 / SC-006 Traceability

Decision: Preserve `MM-542`, DESIGN-REQ-002, DESIGN-REQ-013, and DESIGN-REQ-014 across spec, plan, tasks, and verification.
Evidence: `spec.md` preserves the full Jira preset brief and source design mappings.
Rationale: Downstream verification and PR metadata need stable Jira and source requirement references.
Alternatives considered: Summary-only Jira reference was rejected because the input explicitly requires issue-key preservation.
Test implications: Traceability `rg` check during final verification.
