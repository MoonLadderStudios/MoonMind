# Feature Specification: Temporal Local Artifact System

**Feature Branch**: `045-temporal-artifact-local-dev`  
**Created**: 2026-03-05  
**Status**: Draft  
**Input**: User description: "Implement the local dev version of the artifact system described in docs\Temporal\WorkflowArtifactSystemDesign.md to work with the new Temporal system being added. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §2 "Goals", §15.1 "Workflow rule" | Workflows and activities MUST exchange artifact references and small structured values instead of large blobs in Temporal payloads/history. |
| DOC-REQ-002 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §2 "Goals", §7.4 "Immutability" | Artifacts MUST be immutable (write-once, read-many), and updates MUST create new artifact IDs. |
| DOC-REQ-003 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §6.2 "Decision", §6.3 "Docker Compose default requirement" | Local/dev and default Docker Compose deployments MUST use MinIO as the baseline artifact blob backend; external S3-compatible providers are explicit overrides. |
| DOC-REQ-004 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §6.2 "Decision", §8 "Metadata model" | Artifact metadata/indexing MUST be stored in Postgres, and blob bytes MUST NOT be stored in Postgres. |
| DOC-REQ-005 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §6.3 "Docker Compose default requirement", §9.6 "Local no-auth profile" | Default local one-click deployment MUST include MinIO reachable on the internal Docker network and configured by default for API/worker services. |
| DOC-REQ-006 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §9.5 "App auth mode integration" | With `AUTH_PROVIDER=disabled` default local mode, user-facing artifact metadata/presign endpoints MUST operate without end-user auth and attribute requests to a default local principal for audit consistency. |
| DOC-REQ-007 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §9.2 "Authorization policy", §9.5 "App auth mode integration" | Non-disabled auth modes MUST enforce execution-linked authorization checks before granting artifact metadata or direct blob access. |
| DOC-REQ-008 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §7.1, §7.2, §7.3 | Artifact identity and integrity MUST include opaque `art_<ULID>` IDs, stable storage key layout, and validated `sha256` plus `size_bytes`. |
| DOC-REQ-009 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §8.1, §8.2, §11 | Artifact index metadata MUST support execution linkage and deterministic "latest output" query behavior without mutable workflow execution state hacks. |
| DOC-REQ-010 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §9.1-§9.4, §12.2 | Artifact access MUST use short-lived, scoped presigned URLs and auditable metadata operations. |
| DOC-REQ-011 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §10.1-§10.3 | Upload/download behavior MUST enforce direct-upload and multipart thresholds, and worker/API paths MUST support streaming semantics. |
| DOC-REQ-012 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §12.3 | The system MUST support redaction-aware preview artifacts and restricted raw access behavior for sensitive outputs. |
| DOC-REQ-013 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §13.1-§13.4 | Retention classes, default link-type mappings, and idempotent lifecycle cleanup (soft-delete then hard-delete/tombstone) MUST be implemented. |
| DOC-REQ-014 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §14 "Artifact API contract (REST)" | Artifact API contract MUST cover create/presign/complete/get/list/link/pin/delete behavior with defined request/response constraints. |
| DOC-REQ-015 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §15.2, §16 "Deliverables checklist" | Artifact IO side effects MUST run in activities, and the delivery MUST include runtime-ready API, reference format, and lifecycle controls. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Local Dev Artifact Flow Works End-to-End (Priority: P1)

As a developer running MoonMind locally, I can create, upload, complete, and fetch artifacts through the Artifact API backed by MinIO so Temporal workflows can pass references instead of blob payloads.

**Why this priority**: This is the baseline functionality needed for the new Temporal system to run safely in local/dev mode.

**Independent Test**: Start the default local stack, run artifact create/upload/complete/read/list flows, and confirm returned data is reference-based and linked to executions.

**Acceptance Scenarios**:

1. **Given** default local deployment settings, **When** a client creates and uploads an artifact, **Then** the artifact becomes readable via `ArtifactRef` and appears in execution-linked listing results.
2. **Given** a workflow execution that processes large output, **When** activity results are returned, **Then** workflow state includes only references/small JSON and no large blob payloads.

---

### User Story 2 - Authorization and Preview Safety Match App Mode (Priority: P2)

As an operator, I can rely on consistent artifact authorization behavior across auth modes, including local no-auth defaults and stricter authenticated modes, while keeping sensitive data safe through preview handling.

**Why this priority**: Artifact access control and safe previews prevent data leaks and align with app-level auth behavior required by the design.

**Independent Test**: Run mode-specific tests for `AUTH_PROVIDER=disabled` and authenticated mode to verify presign issuance rules, request attribution, access denials, and preview-vs-raw behavior.

**Acceptance Scenarios**:

1. **Given** `AUTH_PROVIDER=disabled`, **When** a local client calls artifact metadata and presign endpoints, **Then** requests succeed without end-user login and are attributed to the default local principal.
2. **Given** authenticated app mode, **When** an unauthorized principal requests artifact metadata or presigned access, **Then** access is denied and audited.
3. **Given** an artifact marked as restricted for raw content, **When** UI-oriented read flows are requested, **Then** redacted preview artifacts are used by default unless elevated permission is present.

---

### User Story 3 - Retention and Lifecycle Keep Storage Predictable (Priority: P3)

As a platform maintainer, I can rely on retention classes and lifecycle cleanup so local/dev artifact storage stays predictable without breaking execution traceability.

**Why this priority**: Without lifecycle controls, local/dev artifacts will grow unbounded and degrade runtime stability.

**Independent Test**: Seed artifacts across retention classes, run lifecycle cleanup, and verify expected soft/hard deletion behavior and idempotent retry handling.

**Acceptance Scenarios**:

1. **Given** expired non-pinned artifacts, **When** lifecycle cleanup runs repeatedly, **Then** deletion behavior is idempotent and metadata state transitions remain consistent.
2. **Given** artifacts linked by `link_type`, **When** query operations request latest output, **Then** latest artifact selection follows defined deterministic rules.

### Edge Cases

- What happens when a client attempts to upload payloads larger than the direct-upload threshold without multipart completion?
- How does the system behave when multipart upload is initiated but never completed?
- What happens when an artifact is soft-deleted and a download presign is requested afterward?
- How are authorization checks enforced when an artifact link references an execution that no longer has visible permissions for the caller?
- What happens when lifecycle cleanup retries deletion of objects that were already physically removed?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement the artifact system for the Temporal runtime path, not docs-only updates. (Maps: DOC-REQ-015)
- **FR-002**: The delivery MUST include automated validation tests covering artifact creation, upload completion, read/list, and execution linkage behavior in local/dev runtime environments. (Maps: DOC-REQ-015)
- **FR-003**: Workflows and activities MUST exchange `ArtifactRef` values and small JSON only; large blob bytes and presigned URLs MUST NOT be stored in workflow state/history. (Maps: DOC-REQ-001)
- **FR-004**: Artifact data model behavior MUST enforce immutability such that content updates create new artifact IDs and cannot overwrite completed artifacts. (Maps: DOC-REQ-002)
- **FR-005**: Default local/dev and default compose deployments MUST use MinIO as the baseline blob backend, with external S3-compatible providers treated as explicit overrides. (Maps: DOC-REQ-003, DOC-REQ-005)
- **FR-006**: Artifact metadata/index storage MUST use Postgres and MUST NOT store blob bytes in Postgres tables. (Maps: DOC-REQ-004)
- **FR-007**: In `AUTH_PROVIDER=disabled` local mode, user-facing artifact metadata/presign operations MUST be available without end-user auth and MUST attribute requests to the configured default local principal for auditability. (Maps: DOC-REQ-006)
- **FR-008**: In authenticated app modes, artifact metadata and presign access MUST require successful execution-linked authorization checks prior to granting access. (Maps: DOC-REQ-007)
- **FR-009**: Artifact identity and integrity handling MUST include opaque `art_<ULID>` artifact IDs, stable object-key addressing, and persisted/validated digest and size metadata. (Maps: DOC-REQ-008)
- **FR-010**: Artifact metadata and link queries MUST support execution linkage and deterministic "latest output" retrieval rules by execution and link type. (Maps: DOC-REQ-009)
- **FR-011**: Presigned upload/download grants MUST be short-lived, scoped, and auditable, with issuance gated by metadata authorization checks. (Maps: DOC-REQ-010)
- **FR-012**: Upload/download APIs and worker paths MUST enforce direct-vs-multipart size thresholds and support streaming processing for large artifact transfer flows. (Maps: DOC-REQ-011)
- **FR-013**: The runtime MUST support preview generation and access policies that default UI display to redacted preview artifacts when raw content is restricted. (Maps: DOC-REQ-012)
- **FR-014**: Retention classes and default link-type mappings MUST be enforced, and lifecycle cleanup MUST implement idempotent expiration handling with soft-delete and later hard-delete/tombstone behavior. (Maps: DOC-REQ-013)
- **FR-015**: Artifact API behavior MUST cover create, complete, get, presign download/upload-part, execution list, link, pin/unpin, and delete semantics with consistent request/response validation. (Maps: DOC-REQ-014)
- **FR-016**: All artifact byte side effects (read, write, preview generation, execution linking side effects) MUST execute through activity boundaries rather than direct workflow logic. (Maps: DOC-REQ-015)

### Key Entities *(include if feature involves data)*

- **Artifact**: Immutable blob metadata record containing artifact identity, integrity attributes, retention class, and storage addressing.
- **ArtifactRef**: Portable, JSON-safe runtime reference used in workflow/activity input-output contracts.
- **ExecutionRef**: Workflow execution identity (`namespace`, `workflow_id`, `run_id`) used for authorization and listing scopes.
- **Artifact Link**: Relationship record that assigns machine meaning (`link_type`) and optional display label to an artifact within an execution context.
- **Artifact Access Grant**: Authorized, short-lived presign permission constrained to method/key/TTL.
- **Artifact Lifecycle Record**: State and retention metadata used by cleanup jobs to perform idempotent soft/hard deletion behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In default local/dev deployment, at least 95% of artifact create/upload/complete/read/list integration test runs pass on first attempt.
- **SC-002**: 100% of source requirements (`DOC-REQ-001` through `DOC-REQ-015`) are mapped to one or more functional requirements and covered by validation scope.
- **SC-003**: For test artifacts exceeding direct-upload threshold, multipart upload and completion succeed in at least 95% of validation runs without manual intervention.
- **SC-004**: Authorization tests confirm correct mode behavior with zero unauthorized artifact downloads in authenticated mode test cases.
- **SC-005**: Lifecycle cleanup validation shows expired non-pinned artifacts transition to deleted state idempotently across at least two consecutive cleanup runs.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode coverage is explicit and deterministic in `tasks.md`:
  - Production runtime code tasks: `T001-T012`, `T017-T021`, `T025-T029`, `T033-T037`.
  - Validation tasks: `T013-T016`, `T022-T024`, `T030-T032`, `T039-T041`.
- `DOC-REQ-001` through `DOC-REQ-015` keep implementation + validation traceability through:
  - requirement-to-FR mapping in this spec,
  - requirement strategy mapping in `contracts/requirements-traceability.md`,
  - implementation/validation task mapping in the `DOC-REQ Coverage Matrix` in `tasks.md`.

### MEDIUM/LOW remediation status

- Prompt B scope control language has been aligned across `spec.md`, `plan.md`, and `tasks.md` to preserve deterministic runtime-vs-docs behavior.
- Quality gate language explicitly retains runtime task coverage and validation expectations before implementation closure.

### Residual risks

- Artifact-system implementation spans compose, API, Temporal activity, and migration boundaries, so integration drift remains possible until execution-time validation completes.
- Authorization, preview-policy, and lifecycle correctness depend on running full automated validation and runtime scope gates in the target environment.

## Assumptions

- Existing Temporal runtime integration points are available and can accept `ArtifactRef` contracts without introducing Celery-based fallback semantics.
- Local/dev deployments use the repository's default Docker Compose topology.
- Artifact retention durations and limits remain configurable via runtime settings and are not hardcoded to single-environment constants.

## Dependencies

- Temporal workflow/activity runtime components and task queues used by the new system.
- MinIO service availability in default local/dev compose path.
- Postgres availability for artifact index metadata.
- Automated validation infrastructure capable of running unit and integration tests for artifact APIs and activities.
