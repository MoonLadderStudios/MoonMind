# Feature Specification: Manifest Task System Phase 1 (Worker Readiness)

**Feature Branch**: `[030-manifest-phase1]`  
**Created**: March 1, 2026  
**Updated**: March 2, 2026  
**Status**: Active  
**Input**: User description: "Update specs/030-manifest-phase1 to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Strategy Alignment

Phase 0 control-plane work is already present in the repository (manifest queue type, payload normalization, manifest registry CRUD, run submission, and capability-based claim filtering).  
Phase 1 is therefore narrowed to worker-readiness control-plane surfaces that unblock a dedicated manifest worker without introducing the full `manifest_v0` execution engine in this iteration.  
As of March 2, 2026, baseline implementations for these Phase 1 surfaces exist in this branch; this spec keeps them in scope as required runtime deliverables and requires validation coverage before phase closure.

## Source Document Requirements

- **DOC-REQ-001 (ManifestTaskSystem §11.2)**: Manifest-capable workers must be able to resolve profile-backed secret references via `POST /api/queue/jobs/{jobId}/manifest/secrets`, while keeping queue payloads token-free.
- **DOC-REQ-002 (ManifestTaskSystem §11.2)**: Vault references from `manifestSecretRefs.vault` must be returned as pass-through metadata for direct worker-side Vault resolution.
- **DOC-REQ-003 (ManifestTaskSystem §8.11 & §7.1)**: Workers must be able to persist manifest checkpoint state and run metadata back to the registry through a dedicated state callback endpoint.
- **DOC-REQ-004 (Runtime intent guard)**: Delivery must include production runtime code changes and automated validation through `./tools/test_unit.sh`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolve Manifest Secrets at Runtime (Priority: P1)

A manifest worker has claimed a running manifest job and needs profile/env-backed credential values plus vault reference metadata without exposing raw secrets in queue payloads.

**Why this priority**: Without secret resolution, a manifest worker cannot execute authenticated sources/providers safely.

**Independent Test**: Call `POST /api/queue/jobs/{jobId}/manifest/secrets` for a running, claimed manifest job and verify profile refs resolve to values, vault refs are returned as metadata, and invalid job/auth states fail fast.

**Acceptance Scenarios**:

1. **Given** a running manifest job with `manifestSecretRefs.profile` and `manifestSecretRefs.vault`, **When** the owning worker requests secret resolution, **Then** the API returns resolved profile values and vault metadata without returning inline manifest YAML.
2. **Given** a non-manifest job or unclaimed manifest job, **When** the endpoint is called, **Then** the API rejects the request with authorization/state validation errors.

---

### User Story 2 - Persist Manifest Checkpoint State (Priority: P2)

A manifest worker needs to persist incremental checkpoint state and final run metadata so later runs can resume from current state.

**Why this priority**: Incremental sync and observability depend on durable state updates in the registry before the full ingestion worker lands.

**Independent Test**: Call `POST /api/manifests/{name}/state` with `stateJson` and run metadata fields, then `GET /api/manifests/{name}` and verify the updated state and timestamps are persisted.

**Acceptance Scenarios**:

1. **Given** an existing manifest registry entry, **When** the worker posts checkpoint state, **Then** `state_json`, `state_updated_at`, and `updated_at` are updated.
2. **Given** run metadata in the callback payload, **When** state is persisted, **Then** `last_run_*` fields are updated consistently for operator visibility.

---

### User Story 3 - Enforce Worker-Only Secret Access (Priority: P3)

Platform operators need confidence that manifest secret resolution is restricted to manifest-capable workers that own the running job.

**Why this priority**: Secret material must not be retrievable by unrelated workers or non-running jobs.

**Independent Test**: Attempt secret resolution with missing `manifest` capability and with wrong job ownership; verify authorization failures.

**Acceptance Scenarios**:

1. **Given** a worker token without `manifest` capability, **When** it calls the manifest secrets endpoint, **Then** the API returns `403`.
2. **Given** a manifest job claimed by worker A, **When** worker B requests secrets, **Then** the API rejects the request and does not return any secret values.

### Edge Cases

- Profile/env secret references are declared but unresolved at runtime; endpoint must fail with a clear validation error listing unresolved keys.
- Job contains malformed `manifestSecretRefs` payload shape; endpoint must return an empty list for invalid sections rather than crash.
- State updates target a missing manifest name; endpoint must return 404 without mutating queue data.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001, DOC-REQ-002)**: Ensure a worker-facing endpoint `POST /api/queue/jobs/{jobId}/manifest/secrets` exists in `api_service/api/routers/agent_queue.py` and returns `ManifestSecretResolutionResponse`.
- **FR-002 (DOC-REQ-001)**: Secret resolution endpoint must only allow worker-token authenticated callers that advertise `manifest` capability.
- **FR-003 (DOC-REQ-001)**: Secret resolution endpoint must require `job.type == "manifest"`, `job.status == running`, and `job.claimed_by == worker_id` before any secret is resolved.
- **FR-004 (DOC-REQ-001, DOC-REQ-002)**: For each profile reference, resolve values through `AuthProviderManager.get_secret(provider="profile", key=<envKey>, user=<job requester>)`; return vault references as metadata-only pass-through.
- **FR-005 (DOC-REQ-003)**: Ensure `ManifestStateUpdateRequest` schema and `POST /api/manifests/{name}/state` route exist and persist `state_json` and `state_updated_at`.
- **FR-006 (DOC-REQ-003)**: Extend `ManifestsService` with `update_manifest_state(...)` to persist state and optional `last_run_job_id`, `last_run_status`, `last_run_started_at`, and `last_run_finished_at`.
- **FR-007 (DOC-REQ-004)**: Add/extend unit tests for queue secret resolution endpoint and manifest state persistence paths.
- **FR-008 (DOC-REQ-004)**: Validate runtime behavior with `./tools/test_unit.sh`.

### Key Entities *(include if feature involves data)*

- **ManifestSecretResolutionRequest**: Worker request flags controlling inclusion of profile and vault secret sections.
- **ManifestSecretResolutionResponse**: Response envelope containing resolved profile secret values and pass-through vault refs.
- **ManifestStateUpdateRequest**: Registry callback payload carrying `stateJson` and optional run metadata updates.
- **ManifestRecord**: Existing registry record whose `state_json`, `state_updated_at`, and `last_run_*` fields are updated during worker callbacks.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Secret resolution endpoint returns resolved values for profile refs and pass-through vault refs for running/claimed manifest jobs in unit tests.
- **SC-002**: Secret resolution endpoint rejects at least three invalid states in tests: missing manifest capability, wrong ownership, and non-manifest job type.
- **SC-003**: State update endpoint persists `state_json` and updates timestamp/run metadata fields for existing manifests in unit tests.
- **SC-004**: `./tools/test_unit.sh` passes with new and existing tests.

## Assumptions & Constraints

- This phase intentionally does not ship the full `moonmind/manifest_v0` engine or dedicated `moonmind-manifest-worker` daemon.
- Queue payloads remain token-free; secret values are only returned to authorized worker-token callers.
- Existing manifest registry schema columns (`state_json`, `state_updated_at`, `last_run_*`) remain authoritative for callback persistence.
- Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode requirement coverage is explicit and deterministic across artifacts:
  - Production runtime code task coverage in `tasks.md`: `T001-T004`, `T006-T007`, `T010`.
  - Validation task coverage in `tasks.md`: `T005`, `T008-T009`, `T011-T013`.
- `DOC-REQ-*` coverage is explicit:
  - Source requirements (`DOC-REQ-001` through `DOC-REQ-004`) are defined in this spec.
  - Per-`DOC-REQ-*` implementation and validation task mappings are defined in `specs/030-manifest-phase1/contracts/requirements-traceability.md` and mirrored in `tasks.md`.

### MEDIUM/LOW remediation status

- Task descriptions and traceability mappings were normalized to match current runtime file ownership (`moonmind/schemas/agent_queue_models.py` + `api_service/*`) so regeneration remains deterministic.
- Prompt B runtime scope controls were added to `tasks.md` for auditable runtime-vs-validation gate checks.

### Residual risks

- Dedicated `manifest_v0` execution runtime is intentionally deferred, so worker-readiness APIs can be complete while full manifest execution remains future scope.
- Secret provider behavior still depends on profile secret availability at runtime; unresolved refs are intentionally fail-fast and require operator remediation of user profile secrets.
