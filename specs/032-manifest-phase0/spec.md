# Feature Specification: Manifest Queue Phase 0 Alignment

**Feature Branch**: `[029-manifest-phase0]`  
**Created**: February 19, 2026  
**Updated**: March 2, 2026  
**Status**: In Progress  
**Input**: User description: "Update specs/029-manifest-phase0 to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done."
**Runtime Scope Guard**: "Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Context

Phase 0 runtime foundations are already implemented in the repository:

- `manifest` is a first-class Agent Queue job type.
- Manifest payload normalization runs through `moonmind/workflows/agent_queue/manifest_contract.py`.
- `requiredCapabilities`, `manifestHash`, and `manifestVersion` are derived server-side.
- Manifest registry endpoints (`/api/manifests`, `/api/manifests/{name}`, `/api/manifests/{name}/runs`) are available.
- Queue payload serialization is sanitized to avoid exposing inline manifest YAML.

This alignment update narrows 029 scope to a Phase 0 closeout hardening item: actionable validation errors for manifest submission paths, with tests.

## Source Document Requirements

- **DOC-REQ-001 (ManifestTaskSystem §6.1)**: Manifest submissions must use queue type `manifest` with capability-gated worker routing.
- **DOC-REQ-002 (ManifestTaskSystem §6.2)**: Manifest validation must run through dedicated manifest contract logic.
- **DOC-REQ-003 (ManifestTaskSystem §6.5-§6.6)**: Queue payload normalization remains server-derived for capabilities/hash/version.
- **DOC-REQ-004 (ManifestTaskSystem §7.1-§7.2)**: Registry endpoints continue to support validated upsert and run submission.
- **DOC-REQ-005 (Phase 0 runtime guard)**: Runtime changes must ship with automated tests run via `./tools/test_unit.sh`.
- **DOC-REQ-006 (Orchestration runtime guard)**: Deliverables must include production runtime code changes and validation tests; docs/spec-only outcomes are out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Queue Submission Validation Is Actionable (Priority: P1)

An operator submits an invalid manifest queue job and needs an actionable response that explains exactly why validation failed.

**Why this priority**: Queue submission is the primary control-plane entrypoint; opaque errors slow triage and increase retries.

**Independent Test**: Submit invalid manifest payloads to `POST /api/queue/jobs` and verify HTTP 422 responses use manifest-specific error codes and include contract messages.

**Acceptance Scenarios**:

1. **Given** a malformed manifest payload, **When** `POST /api/queue/jobs` is called with `type="manifest"`, **Then** the API responds 422 with `code="invalid_manifest_job"` and an actionable message.
2. **Given** a non-manifest queue payload fails validation, **When** submitted, **Then** existing generic queue error behavior remains unchanged.

---

### User Story 2 - Registry Upsert Validation Is Actionable (Priority: P2)

An operator upserts invalid manifest YAML and needs the API response to include the manifest contract validation reason.

**Why this priority**: Registry is the canonical manifest source; clear failures are required for safe governance and fast correction.

**Independent Test**: Call `PUT /api/manifests/{name}` with invalid YAML/contract content and verify 422 returns the manifest contract message.

**Acceptance Scenarios**:

1. **Given** invalid manifest YAML, **When** `PUT /api/manifests/{name}` is called, **Then** the API responds 422 with `code="invalid_manifest"` and a descriptive message.

### Edge Cases

- Validation messages must not include raw secret values; only safe contract errors should be surfaced.
- Non-manifest queue job validation must continue returning `invalid_queue_payload` to preserve existing clients.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)**: Keep existing Phase 0 manifest runtime behavior as-is (job type, normalization, capability derivation, sanitization, registry flows).
- **FR-002 (DOC-REQ-002)**: `POST /api/queue/jobs` must return manifest-specific 422 errors (`code="invalid_manifest_job"`) with contract-derived message text when `type="manifest"` validation fails.
- **FR-003 (DOC-REQ-004)**: `PUT /api/manifests/{name}` must return 422 `invalid_manifest` responses with descriptive contract error text.
- **FR-004 (DOC-REQ-002, DOC-REQ-004)**: Error handling changes must not alter successful response payload shapes for queue or manifest registry endpoints.
- **FR-005 (DOC-REQ-005)**: Add/update unit tests validating FR-002 and FR-003, and execute `./tools/test_unit.sh`.
- **FR-006 (DOC-REQ-006)**: The feature must include at least one production runtime code change in API request validation paths plus accompanying validation tests; spec-only/doc-only updates do not satisfy completion.

### Key Entities

- **ManifestValidationErrorResponse**: HTTP 422 response envelope with stable `code` and actionable `message` for manifest submission failures.
- **QueueValidationErrorResponse**: Existing generic queue 422 envelope that remains unchanged for non-manifest job types.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Invalid manifest queue submissions return HTTP 422 with `invalid_manifest_job` and descriptive contract message in automated tests.
- **SC-002**: Invalid registry upserts return HTTP 422 with `invalid_manifest` and descriptive contract message in automated tests.
- **SC-003**: Existing non-manifest queue validation tests continue to pass with unchanged response code semantics.
- **SC-004**: `./tools/test_unit.sh` passes after these changes.
- **SC-005**: Task completion evidence shows both runtime code diff(s) and test diff(s) tied to this feature.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime mode guard is explicit and deterministic in `tasks.md` with production runtime code tasks (`T003`, `T004`, `T007`, `T008`, `T011`, `T012`) and validation tasks (`T005`, `T006`, `T009`, `T010`, `T015`).
- Every `DOC-REQ-*` now has implementation and validation task coverage in `contracts/requirements-traceability.md`, including explicit task IDs for traceable execution.
- Spec requirements, plan constraints, and tasks coverage all preserve Phase 0 scope: harden validation ergonomics without introducing new execution behavior.

### MEDIUM/LOW remediation status

- Added explicit Prompt B scope controls in `tasks.md` so runtime-vs-docs intent remains visible during future task regeneration.
- Strengthened traceability language so requirement coverage is audited by deterministic task mappings, not only descriptive implementation surfaces.

### Residual risks

- Contract-derived message text can change as validation rules evolve, so tests should continue focusing on stable codes plus actionable message presence.
- This feature intentionally depends on pre-existing manifest routing/normalization behavior; broader regressions remain guarded by the full unit suite.

## Assumptions & Constraints

- Manifest contract messages are already sanitized and safe to expose.
- This alignment feature does not introduce new manifest execution behavior; it hardens API error ergonomics for existing Phase 0 runtime flows.
