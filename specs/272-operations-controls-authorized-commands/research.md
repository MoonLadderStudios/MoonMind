# Research: Operations Controls Exposed as Authorized Commands

## FR-001 / DESIGN-REQ-002 Operations Command Surface

Decision: Partial implementation exists; add the missing worker-pause backend command route.
Evidence: `frontend/src/components/settings/OperationsSettingsSection.tsx` renders Operations, worker controls, and deployment controls. `api_service/api/routers/task_dashboard.py` configures `/api/system/worker-pause` for Settings. `rg` found no route implementation for `/api/system/worker-pause`.
Rationale: The UI already treats Settings as the discovery surface, but without the backend route the configured worker operation cannot be invoked through the runtime surface.
Alternatives considered: Reusing deployment operation endpoints was rejected because worker pause is a separate operational subsystem and should not be modeled as deployment update.
Test implications: Unit API, unit UI, and an integration route test.

## FR-002 / SC-001 Operations State And Feedback

Decision: Partial UI behavior exists; extend backend response and UI tests to cover operation metadata available from the route.
Evidence: The UI displays worker state, mode, reason, updated time, queued/running/stale/drained metrics, and recent actions. `WorkerPauseSnapshotResponse` already has system, metrics, audit, and `signalStatus`.
Rationale: Existing schemas are close to the desired response shape but need route-backed actor/audit/status evidence.
Alternatives considered: Creating a separate dashboard-only response shape was rejected because `WorkerPauseSnapshotResponse` is already referenced by the configured endpoint.
Test implications: Unit UI assertions and API schema tests.

## FR-003 / SC-002 Confirmation Requirements

Decision: Backend confirmation enforcement is missing for worker-pause commands and should be added where actions are disruptive.
Evidence: UI requires a reason for pause/resume and confirms unsafe resume when workers are not drained. Deployment rollback enforces confirmation in `DeploymentOperationsService`.
Rationale: Backend enforcement is required because hidden or manipulated UI is not a security boundary.
Alternatives considered: Client-only confirmation was rejected by DESIGN-REQ-014 and FR-005.
Test implications: Unit API tests for missing confirmation and unit UI test for submitted confirmation fields.

## FR-004 / SC-004 Operation Command Metadata And Audit

Decision: Add a typed operation command model and persist compact audit events using `SettingsAuditEvent`.
Evidence: `api_service/db/models.py` defines `SettingsAuditEvent` with event type, key, scope, actor, old/new JSON, reason, request ID, and timestamp. No worker-pause route writes audit entries today.
Rationale: The story needs auditable actions but does not justify a new table. Existing settings audit events can store non-secret operation metadata without schema migration.
Alternatives considered: In-memory audit was rejected because the source design asks for audit trail. A new table was rejected because the existing table is sufficient for compact operation events.
Test implications: Unit service/API tests for persisted audit and sanitized latest action response.

## FR-005 / FR-006 / SC-003 Authorization

Decision: Mirror the deployment operations authorization pattern for worker-pause operations.
Evidence: `api_service/api/routers/deployment_operations.py` uses `_require_admin` to reject non-superusers when auth is enabled, while allowing disabled-auth local development. No worker-pause route exists.
Rationale: This satisfies backend authorization without inventing a larger RBAC model in this story.
Alternatives considered: Frontend-only control hiding was rejected. A new permissions table was rejected as out of scope.
Test implications: Unit API test for non-admin 403 and no subsystem invocation.

## FR-007 / SC-005 Operational Subsystem Ownership

Decision: Route worker quiesce/resume through `TemporalExecutionService.send_quiesce_pause_signal` and `send_resume_signal`; drain mode records the system block without broadcasting pause signals.
Evidence: `moonmind/workflows/temporal/service.py` exposes quiesce pause and resume signal methods. `create_execution` already references `/api/system/worker-pause` for blocked submissions.
Rationale: Temporal remains the subsystem boundary for workflow pause/resume signaling. Settings only invokes a typed command route.
Alternatives considered: Direct workflow manipulation from the React component was rejected because Settings must not own operational semantics.
Test implications: Unit API tests assert the Temporal service methods are called for quiesce/resume and not called for unauthorized requests.

## FR-008 Operation Result Statuses

Decision: Return normalized command statuses from the worker-pause route and map subsystem failures to sanitized unavailable/failed responses.
Evidence: Deployment operation route already maps queue/validation failures to typed HTTP responses. Worker-pause schemas have `signalStatus`.
Rationale: Operator-visible results must distinguish success, failure, unauthorized, conflict, and unavailable outcomes enough for the UI and tests.
Alternatives considered: Returning raw exceptions was rejected because diagnostics must be sanitized.
Test implications: Unit API tests for success and service failure.

## FR-009 Sanitized Audit

Decision: Store non-secret operation metadata only and return latest audit events without credentials or raw environment data.
Evidence: `SettingsAuditEvent` supports redaction and JSON metadata; deployment recent actions suppress raw command logs unless explicitly permitted.
Rationale: Operations audit must be useful without exposing secrets.
Alternatives considered: Logging full command payloads was rejected by security guardrails.
Test implications: Unit service test checks audit metadata fields and absence of secret-like raw payloads.

## FR-010 / SC-006 Traceability

Decision: Preserve `MM-542`, DESIGN-REQ-002, DESIGN-REQ-013, and DESIGN-REQ-014 across spec, plan, tasks, and verification.
Evidence: `spec.md` preserves the full Jira preset brief and source design mappings.
Rationale: Downstream verification and PR metadata need stable Jira and source requirement references.
Alternatives considered: Summary-only Jira reference was rejected because the input explicitly requires issue-key preservation.
Test implications: Traceability `rg` check during final verification.
