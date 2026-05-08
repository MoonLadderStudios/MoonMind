# Research: Route Binary Inputs Through Authorized Artifact Refs

## Setup Script

Decision: Use `SPECIFY_FEATURE=321-route-binary-artifact-refs .specify/scripts/bash/setup-plan.sh --json` for setup.
Evidence: Running `.specify/scripts/bash/setup-plan.sh --json` directly failed because the managed branch name is `run-jira-orchestrate-for-mm-628-route-bi-6bcc12c5`; with `SPECIFY_FEATURE` set, the script resolved `specs/321-route-binary-artifact-refs` and created `plan.md`.
Rationale: The feature directory and active spec are already valid; the direct failure is a branch naming guard, not a missing feature artifact.
Alternatives considered: Renaming or switching branches was rejected because this managed step is only authorized to plan.
Test implications: None.

## FR-001 Artifact Upload Intents

Decision: Implemented verified.
Evidence: `api_service/api/routers/temporal_artifacts.py` exposes `POST /api/artifacts`; `frontend/src/entrypoints/task-create.test.tsx` asserts objective and step attachment selections call `/api/artifacts` with attachment metadata.
Rationale: The browser path and API endpoint for creating upload intents already exist and have focused UI/API coverage.
Alternatives considered: Planning new upload-intent APIs was rejected because the existing artifact API already provides the required surface.
Test implications: None beyond final verification unless adjacent changes alter upload orchestration.

## FR-002 / FR-005 Binary Bytes Stay Out Of Instructions And Execution Payloads

Decision: Implemented verified.
Evidence: `moonmind/workflows/temporal/artifacts.py` stores bytes through `TemporalArtifactStore`; `task-create.test.tsx` asserts task and step instructions are not rewritten with attachment text and that submissions contain `inputAttachments` refs; `tests/unit/api/routers/test_executions.py` verifies attachment refs reach initial parameters.
Rationale: Existing behavior directly matches the requirement that execution receives structured refs rather than binary bytes or credentials.
Alternatives considered: Adding a new binary payload sanitizer was rejected because structured attachment refs are already the boundary.
Test implications: None beyond final traceability unless implementation touches payload composition.

## FR-003 / DESIGN-REQ-007 Upload Completion Before Submission

Decision: Implemented verified for browser ordering, implemented unverified for binary-ref API finalization coverage.
Evidence: `task-create.test.tsx` asserts presigned upload and `/api/artifacts/{id}/complete` occur before `/api/executions`; `api_service/api/routers/executions.py` rejects artifacts whose status is not `COMPLETE`.
Rationale: Browser ordering is covered, but MM-628 should add focused API tests for pending/failed/deleted binary refs before relying on this as a full boundary guarantee.
Alternatives considered: UI-only verification was rejected because API submission must remain authoritative.
Test implications: Unit and integration tests for pending/failed/deleted refs.

## FR-004 / FR-006 Invalid, Unfinalized, And Unauthorized Uploads

Decision: Partial.
Evidence: `api_service/api/routers/executions.py` validates artifact status, content type, size, duplicate refs, max count, max bytes, and policy enablement; existing tests cover several invalid cases. Authorization of a submitted artifact ref by the current principal and execution scope is not proven in the submission/linking path.
Rationale: MM-628 explicitly requires unauthorized refs to be rejected before execution. Existing status/content validation is strong, but ownership or view-permission checks are not clearly enforced before linking input artifacts to an execution.
Alternatives considered: Deferring authorization to preview/download was rejected because the spec requires rejection before execution submission for unauthorized refs.
Test implications: Unit and integration tests must cover another user's completed artifact, a service principal path, and execution creation/link rejection.

## FR-007 / FR-008 Browser Preview And Download Authorization

Decision: Partial.
Evidence: `api_service/api/routers/temporal_artifacts.py` exposes metadata, presign-download, and download endpoints; `TemporalArtifactService` has owner/service read checks and restricted raw access policy; `tests/integration/temporal/test_temporal_artifact_auth_preview.py` covers preview metadata for restricted artifacts.
Rationale: Browser access is routed through MoonMind APIs, but execution ownership/view permission behavior for linked input artifacts needs focused proof.
Alternatives considered: Marking fully verified was rejected because owner-only checks do not necessarily prove execution viewer permission semantics.
Test implications: Unit and integration tests for owner, non-owner, service, and execution-linked artifact reads.

## FR-009 Worker Materialization Authorization

Decision: Implemented unverified.
Evidence: `moonmind/agents/codex_worker/worker.py` collects and materializes input attachments through the queue client; `tests/unit/agents/codex_worker/test_attachment_materialization.py` verifies target-aware downloads, manifests, and failure diagnostics.
Rationale: Materialization behavior exists, but the tests use a fake queue client and do not prove service-principal authorization or absence of browser-visible credentials in the worker read path.
Alternatives considered: Treating fake queue download coverage as sufficient was rejected because MM-628 names service credentials and execution authorization.
Test implications: Add worker/API boundary proof that materialization uses service-authorized artifact reads.

## FR-010 Execution-Scoped Artifact Links

Decision: Partial.
Evidence: `api_service/api/routers/executions.py` links submitted input artifacts to execution records with `link_type="input.attachment"` and appends IDs to `record.artifact_refs`.
Rationale: Links exist, but the requirement that refs cannot be reused outside their authorized execution context needs explicit enforcement and tests.
Alternatives considered: Relying on artifact IDs alone was rejected because the source design says artifact links are execution-scoped.
Test implications: Integration tests for linked execution reads and unauthorized cross-execution reuse.

## FR-011 Metadata Is Observability Only

Decision: Implemented verified.
Evidence: `docs/Tasks/TaskArchitecture.md` states target binding comes from task contract and snapshot semantics; `tests/unit/agents/codex_worker/test_attachment_materialization.py` verifies materialization paths derive from objective/step refs and are stable across unrelated target order.
Rationale: Existing task contract and worker tests preserve target meaning independently of storage paths.
Alternatives considered: Adding metadata-only target inference was rejected because it would contradict the source design.
Test implications: None beyond final traceability.

## FR-012 / SC-006 Traceability

Decision: Implemented verified.
Evidence: `specs/321-route-binary-artifact-refs/spec.md` preserves MM-628, the original Jira preset brief, and DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, and DESIGN-REQ-022; this plan and design artifacts preserve the same IDs.
Rationale: Traceability is present and must be carried forward by tasks, implementation notes, verification output, commit text, and PR metadata.
Alternatives considered: None.
Test implications: Final traceability check.

## Test Tooling

Decision: Use repository-standard unit and hermetic integration runners, with focused UI iteration routed through the unit wrapper.
Evidence: AGENTS.md requires `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration CI. It also allows `./tools/test_unit.sh --ui-args <path>` for targeted Vitest files.
Rationale: The story crosses frontend upload orchestration, FastAPI artifact/execution APIs, artifact service authorization, and worker materialization; unit and integration strategies must be separate.
Alternatives considered: Nested Docker unit testing was rejected by managed-agent guidance.
Test implications: Write failing focused tests first, then run targeted tests, final `./tools/test_unit.sh`, and relevant `./tools/test_integration.sh`.
