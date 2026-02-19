# Feature Specification: Manifest Queue Plumbing (Phase 0)

**Feature Branch**: `028-manifest-queue`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 of the manifest task system as described in docs/ManifestTaskSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Queue Accepts Manifest Jobs (Priority: P1)

As a platform engineer submitting ingestion runs, I need the Agent Queue API to accept `type="manifest"` jobs with validated payloads so manifest runs can be orchestrated alongside other tasks without manual overrides.

**Why this priority**: No manifest work can progress unless the queue allows manifest job types with correctly derived capabilities and payload guards.

**Independent Test**: Submit a POST `/api/queue/jobs` request with a valid manifest payload and verify the API persists the job, stores derived capability metadata, and exposes it through queue detail APIs without touching codex/gemini job code paths.

**Acceptance Scenarios**:

1. **Given** `_SUPPORTED_QUEUE_JOB_TYPES` only includes historical job types, **When** the manifest feature is enabled, **Then** the set includes `manifest` and `AgentQueueService.create_job` routes manifest payloads to the manifest normalization path.
2. **Given** a manifest payload containing YAML and metadata, **When** the API processes it, **Then** it rejects requests where `manifest.name` and `manifest.metadata.name` differ, ensuring point IDs remain deterministic.

---

### User Story 2 - API Normalizes Manifest Payloads (Priority: P1)

As an API maintainer, I need a dedicated manifest contract module that validates manifests, derives `requiredCapabilities`, and enforces token-free payload rules so workers only see properly normalized jobs.

**Why this priority**: Without manifest-specific normalization, jobs would leak through task-only validation or allow raw secrets, causing worker claim failures or policy violations.

**Independent Test**: Unit tests import the manifest contract module inside the Agent Queue workflow package, pass sample manifests (`inline` and registry references), and verify the normalized payload adds `manifestHash`, `manifestVersion`, and capability arrays without mutating legacy task submissions.

**Acceptance Scenarios**:

1. **Given** manifests referencing multiple data sources, **When** normalization runs, **Then** `derive_required_capabilities` returns `manifest`, embeddings provider capabilities, and per-source capabilities that match Section 6.5 of the contract doc.
2. **Given** a payload with queue options requesting `dryRun` and `maxDocs`, **When** normalization merges manifest YAML `run` values with `payload.manifest.options`, **Then** only run-control fields are overridden and structural manifest sections remain unchanged.

---

### User Story 3 - Manifest Registry CRUD + Run Submission (Priority: P2)

As an operator managing manifests, I need minimal `/api/manifests` endpoints to create, read, and run manifests so I can keep YAML in Postgres and trigger ingestion jobs without filing backend requests.

**Why this priority**: Registry-backed runs unlock reuse/governance, but Phase 0 must at least persist manifests and trigger queue jobs to unblock worker development.

**Independent Test**: `PUT /api/manifests/{name}` accepts YAML, stores hash/version metadata, and `POST /api/manifests/{name}/runs` creates a manifest job referencing the stored content while enforcing the same normalization and validation rules as inline submissions.

**Acceptance Scenarios**:

1. **Given** the registry already stores a manifest, **When** a user calls `POST /api/manifests/{name}/runs`, **Then** the API fetches the canonical YAML, computes hashes, creates the queue job, and returns the job id for dashboard linking.
2. **Given** a `GET /api/manifests/{name}` request, **When** the manifest exists, **Then** the response includes YAML, version metadata, last run pointers, and checkpoint state placeholders as described in Section 7.1.

---

### Edge Cases

- What happens when a manifest references capabilities that no worker advertises? The manifest contract must still derive capabilities and queue submissions SHOULD succeed, but operators need a validation warning (event and response) that jobs will remain unclaimed unless a worker advertises matching capabilities.
- How does the API handle malformed or mismatched manifests (e.g., YAML without `metadata.name` or payload name mismatch)? Requests are rejected with descriptive validation errors before jobs are enqueued.
- What happens when registry YAML changes between submission and run start? Phase 0 stores `manifestHash` and `manifestVersion` alongside each job so workers detect drift; future phases can add invalidation hooks.

### Dependencies & Assumptions

- The existing Agent Queue service, authentication, and artifact subsystems remain available; Phase 0 extends them without replacing current task functionality.
- The `manifest` Postgres table already exists from legacy manifest sync services and can be extended without destructive migrations.
- Worker implementations (Phase 1) are out of scope but will rely on the metadata and capabilities produced here, so payload contracts must be forward-compatible.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Update the Agent Queue allowlist so `manifest` is treated as a first-class job type, and ensure `AgentQueueService.create_job` routes manifest jobs through manifest-specific normalization without impacting existing types.
- **FR-002**: Create an Agent Queue manifest contract module containing `ManifestContractError`, `normalize_manifest_job_payload()`, and `derive_required_capabilities()` as defined in docs/ManifestTaskSystem.md Section 6.
- **FR-003**: Manifest normalization MUST enforce token-free payloads, verify `payload.manifest.name` matches YAML `metadata.name`, compute `manifestHash`, determine `manifestVersion`, and merge run-control options according to Section 6.2.3.
- **FR-004**: Extend `AgentQueueService` so `create_job()` uses the manifest contract to derive `requiredCapabilities` (including data sources, embeddings provider, vector store, `manifest`) and persists results alongside the queue job record.
- **FR-005**: Add `/api/manifests` endpoints: `GET /api/manifests`, `GET /api/manifests/{name}`, `PUT /api/manifests/{name}`, and `POST /api/manifests/{name}/runs`, wired into the existing FastAPI layer with input validation, hashing, and linkage to queue jobs.
- **FR-006**: Ensure registry endpoints update `ManifestRecord` columns (`version`, `content_hash`, `last_run_job_id`, timestamps, `state_json`) without data loss and return metadata needed for operators to inspect manifest health.
- **FR-007**: Provide unit tests covering manifest job creation, capability derivation, registry CRUD, and error paths so regression detection runs through `./tools/test_unit.sh`.

### Key Entities *(include if feature involves data)*

- **ManifestJobPayload**: JSON structure accepted by `/api/queue/jobs` when `type="manifest"`; contains manifest YAML/source info, options overrides, `manifestHash`, `manifestVersion`, and derived capabilities.
- **ManifestContract**: Manifest-specific validation layer responsible for hashing, capability derivation, name enforcement, and allowed overrides inside the Agent Queue workflow package.
- **ManifestRecord**: Postgres model storing manifest YAML, version/hash metadata, last run references, and checkpoint state fields used by registry endpoints.
- **Agent Queue Job**: Database entry representing work items; now includes manifest job rows with `type="manifest"`, derived capabilities, and manifest-specific payload metadata so workers can claim runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Creating a manifest via `PUT /api/manifests/{name}` followed by `POST /api/manifests/{name}/runs` results in a persisted queue job whose payload contains normalized manifest fields (`manifestHash`, `requiredCapabilities`, version) and the job appears in queue listings under the manifest category.
- **SC-002**: Direct `POST /api/queue/jobs` submissions with `type="manifest"` pass validation only when payload name and YAML name match, and they expose derived capabilities identical to `derive_required_capabilities()` test fixtures.
- **SC-003**: Automated tests executed via `./tools/test_unit.sh` cover manifest job normalization, allowed options precedence, registry CRUD, and rejection paths, demonstrating runtime code plus validation tests are present.
- **SC-004**: Queue detail APIs and generated artifacts expose manifest metadata without raw secrets, evidenced by payload snapshots or tests verifying token-free normalization.
