# Research: Temporal Local Artifact System

## Decision 1: MinIO is the default local/dev blob backend

- **Decision**: Make MinIO the baseline artifact store for local/dev and default Docker Compose flows.
- **Rationale**: Satisfies `DOC-REQ-003` and `DOC-REQ-005`, aligns with one-click local deployment, and keeps behavior close to production S3-compatible storage semantics.
- **Alternatives considered**:
  - Keep local filesystem as baseline: rejected because it conflicts with source requirements and multipart/presign expectations.
  - Require external S3 provider for local dev: rejected because it adds setup friction and violates one-click default goals.

## Decision 2: Keep Postgres as metadata/index only, never blob bytes

- **Decision**: Continue storing artifact metadata, links, and pin records in Postgres; store artifact bytes only in MinIO/S3-compatible storage.
- **Rationale**: Enforces `DOC-REQ-004`, keeps DB footprint predictable, and preserves OLTP behavior.
- **Alternatives considered**:
  - Store bytes in Postgres `bytea`: rejected due to growth/performance and explicit non-goal.
  - Hybrid byte fallback in DB: rejected because it creates two storage truths and complicates lifecycle logic.

## Decision 3: Preserve immutable `ArtifactRef` contract (`art_<ULID>`)

- **Decision**: Keep opaque artifact IDs and immutable completion semantics where updates create new artifacts.
- **Rationale**: Covers `DOC-REQ-002` and `DOC-REQ-008`, and avoids mutable-state ambiguity in workflow history.
- **Alternatives considered**:
  - Allow overwrite by artifact ID: rejected because it breaks immutability and traceability.
  - Content-hash-as-primary-key only: rejected because storage key evolution and metadata lifecycle need decoupled identity.

## Decision 4: Align auth behavior to app auth mode

- **Decision**: In `AUTH_PROVIDER=disabled` mode, allow user-facing artifact metadata/presign operations without end-user login and attribute to the local default principal; in authenticated modes, require principal-based authorization checks.
- **Rationale**: Required by `DOC-REQ-006` and `DOC-REQ-007` and consistent with app-level auth behavior.
- **Alternatives considered**:
  - Force auth in all modes: rejected because it breaks default one-click local flow.
  - Disable authorization checks in authenticated modes: rejected due to explicit security requirements.

## Decision 5: Authorization is execution-linked and auditable

- **Decision**: Gate metadata and presign issuance on execution-linked visibility plus ownership/service-role checks; log principal, artifact ID, operation, and execution linkage.
- **Rationale**: Implements `DOC-REQ-007` and `DOC-REQ-010` while keeping policy explainable.
- **Alternatives considered**:
  - Ownership-only authorization: rejected because linked execution visibility is required.
  - Presign without metadata authorization: rejected due to direct-access risk.

## Decision 6: Direct upload + multipart threshold policy is explicit

- **Decision**: Keep direct upload size threshold configurable and require multipart/presigned part flows for larger artifacts; support streaming read/write paths.
- **Rationale**: Meets `DOC-REQ-011` and prevents large in-memory buffering paths.
- **Alternatives considered**:
  - Single-shot uploads only: rejected because large artifact reliability degrades.
  - Always multipart: rejected because small local artifacts should remain simple and fast.

## Decision 7: Redaction preview artifacts are first-class

- **Decision**: Use `artifact.compute_preview` activity behavior to create redacted preview artifacts and default UI-facing reads to preview for restricted raw content.
- **Rationale**: Satisfies `DOC-REQ-012` and lowers leak risk for sensitive outputs.
- **Alternatives considered**:
  - Inline redaction at read time only: rejected because preview artifacts should be auditable and reusable.
  - No preview path, raw-only access: rejected because it conflicts with redaction strategy requirements.

## Decision 8: Lifecycle cleanup is idempotent and schedule-driven

- **Decision**: Implement lifecycle cleanup with soft-delete semantics first and hard-delete/tombstone follow-up, safe for repeated retries.
- **Rationale**: Required by `DOC-REQ-013`; supports robust retries and predictable storage behavior.
- **Alternatives considered**:
  - Immediate hard-delete only: rejected because soft-delete and audit controls are required.
  - Manual-only cleanup: rejected due to operational drift and storage growth risk.

## Decision 9: Artifact API contract must be complete for Temporal runtime

- **Decision**: Keep/create API surfaces for create/presign/complete/get/list/link/pin/delete and include upload-part presign for multipart compatibility.
- **Rationale**: Directly maps to `DOC-REQ-014` and integration deliverables in `DOC-REQ-015`.
- **Alternatives considered**:
  - Partial API with internal-only endpoints: rejected because client and operator flows need full contract coverage.
  - Only docs contract with deferred runtime implementation: rejected by runtime intent and FR-001.

## Decision 10: Activity boundaries enforce workflow payload safety

- **Decision**: Keep artifact byte side effects inside activities and pass only `ArtifactRef` + small JSON through workflow state/history.
- **Rationale**: Implements `DOC-REQ-001` and `DOC-REQ-015`; preserves deterministic Temporal workflow behavior.
- **Alternatives considered**:
  - Workflow-side blob handling: rejected due to payload/history bloat and determinism concerns.
  - Presigned URL persistence in workflow state: rejected because URLs are short-lived and security-scoped.

## Decision 11: Runtime-vs-docs behavior must stay mode aligned

- **Decision**: Keep runtime mode as the selected orchestration mode for this feature; require production code changes and validation tests. Document docs-mode scope-check skip behavior for completeness only.
- **Rationale**: Matches feature intent and prevents false completion through docs-only updates.
- **Alternatives considered**:
  - Allow docs-only closure: rejected as non-compliant with FR-001/FR-002 and runtime objective.
  - Ignore mode semantics in planning artifacts: rejected because consistency across spec/plan/tasks is required.
