# Feature Specification: Agent Queue Artifact Upload (Milestone 2)

**Feature Branch**: `010-agent-queue-artifacts`  
**Created**: 2026-02-13  
**Status**: Draft  
**Input**: User description: "Implement Milestone 2 of docs/CodexTaskQueue.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Worker Uploads Job Artifacts (Priority: P1)

As a remote worker, I can upload output files for a queue job so execution logs and patches are available in MoonMind.

**Why this priority**: Artifact upload is the primary Milestone 2 deliverable and required for cross-machine execution.

**Independent Test**: Use `POST /api/queue/jobs/{jobId}/artifacts/upload` with multipart form fields and verify metadata is persisted and file is stored under the job artifact root.

**Acceptance Scenarios**:

1. **Given** a valid queue job and authenticated caller, **When** upload endpoint receives a file and relative artifact name, **Then** the file is stored under the job artifact directory and metadata is recorded.
2. **Given** multipart fields include optional content type and digest, **When** upload succeeds, **Then** the response includes those values in artifact metadata.

---

### User Story 2 - Operator Lists and Downloads Artifacts (Priority: P1)

As an operator, I can list artifacts for a job and download a selected artifact so I can inspect run outcomes.

**Why this priority**: Upload without retrieval does not complete the artifact ingestion workflow.

**Independent Test**: After uploading artifacts, call list and download endpoints and verify metadata inventory and file payload retrieval.

**Acceptance Scenarios**:

1. **Given** a job with uploaded artifacts, **When** `GET /api/queue/jobs/{jobId}/artifacts` is called, **Then** response returns all artifact metadata for that job.
2. **Given** a valid job and artifact id, **When** `GET /api/queue/jobs/{jobId}/artifacts/{artifactId}/download` is called, **Then** API returns the stored file with appropriate filename/content type headers.

---

### User Story 3 - Artifact Path and Size Safety (Priority: P2)

As a platform owner, I need upload paths and file sizes validated so workers cannot abuse artifact ingestion.

**Why this priority**: Security and reliability are mandatory for file uploads and explicitly required in Milestone 2.

**Independent Test**: Submit uploads with traversal payloads and oversized files; verify requests are rejected and storage remains constrained to job roots.

**Acceptance Scenarios**:

1. **Given** an artifact name containing traversal tokens, **When** upload is attempted, **Then** API rejects the request and no file is written outside the job root.
2. **Given** a file larger than configured upload limit, **When** upload is attempted, **Then** API returns validation error and does not persist metadata.

### Edge Cases

- Upload request references a nonexistent job id.
- Download request uses artifact id that belongs to a different job.
- Duplicate artifact names are uploaded for same job.
- Optional content type is omitted and API must derive a safe default.
- Artifact listing request targets a job with zero artifacts.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/CodexTaskQueue.md:463`, `docs/CodexTaskQueue.md:465`): Milestone 2 MUST add an artifact storage root and upload endpoint.
- **DOC-REQ-002** (Source: `docs/CodexTaskQueue.md:201`, `docs/CodexTaskQueue.md:203`): Upload endpoint MUST be `POST /api/queue/jobs/{jobId}/artifacts/upload` and accept multipart form data.
- **DOC-REQ-003** (Source: `docs/CodexTaskQueue.md:205`, `docs/CodexTaskQueue.md:208`): Multipart upload MUST support `file`, `name`, and optional `contentType`/`digest`.
- **DOC-REQ-004** (Source: `docs/CodexTaskQueue.md:209`, `docs/CodexTaskQueue.md:211`): Server MUST store files under `${ARTIFACT_ROOT}/agent_jobs/<jobId>/<name>`.
- **DOC-REQ-005** (Source: `docs/CodexTaskQueue.md:466`, `docs/CodexTaskQueue.md:212`): System MUST persist artifact metadata in an `agent_job_artifacts` table or equivalent metadata structure.
- **DOC-REQ-006** (Source: `docs/CodexTaskQueue.md:214`, `docs/CodexTaskQueue.md:217`, `docs/CodexTaskQueue.md:467`): API MUST expose list and download endpoints for job artifacts.
- **DOC-REQ-007** (Source: `docs/CodexTaskQueue.md:219`, `docs/CodexTaskQueue.md:223`, `docs/CodexTaskQueue.md:468`): Artifact storage MUST enforce per-job roots, path traversal protections, and upload size limits.
- **DOC-REQ-008** (Source: `docs/CodexTaskQueue.md:224`, `docs/CodexTaskQueue.md:228`): Configuration MUST include dedicated artifact root setting `AGENT_JOB_ARTIFACT_ROOT` defaulting to `var/artifacts/agent_jobs`.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`): The system MUST provide authenticated multipart artifact upload for queue jobs with required and optional metadata fields.
- **FR-002** (`DOC-REQ-004`, `DOC-REQ-008`): The system MUST store artifacts under a dedicated configurable root partitioned by job id.
- **FR-003** (`DOC-REQ-005`): The system MUST persist artifact metadata records linked to queue jobs, including storage path and optional content metadata.
- **FR-004** (`DOC-REQ-006`): The system MUST provide artifact listing and artifact download endpoints scoped to a queue job.
- **FR-005** (`DOC-REQ-007`): The system MUST reject traversal paths, cross-job artifact access, and uploads exceeding configured size limits.
- **FR-006**: Runtime deliverables MUST include production code changes and validation tests; documentation-only changes are insufficient.

### Key Entities *(include if feature involves data)*

- **AgentJobArtifact**: Metadata record representing one uploaded artifact for a queue job (name, storage path, content metadata, size, digest, timestamps).
- **ArtifactUploadRequest**: Multipart request containing binary file and path metadata validated against job-scoped storage policy.
- **ArtifactStorageConfig**: Runtime settings controlling root path and maximum upload size.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: API accepts valid multipart uploads and stores files under job-scoped directories rooted at configured artifact root.
- **SC-002**: Artifact metadata is queryable by job and returned by list endpoint.
- **SC-003**: Download endpoint returns uploaded artifact bytes for valid job/artifact pairs and rejects mismatched pairs.
- **SC-004**: Automated tests verify traversal rejection and configured file-size enforcement.
- **SC-005**: Milestone 2 unit tests pass through `./tools/test_unit.sh`.

## Assumptions

- Milestone 2 builds on Milestone 1 queue job APIs and extends them with artifact ingestion/retrieval.
- Artifact payload storage remains local filesystem-backed in this milestone (object storage deferred).
