# Feature Specification: Manifest Queue Phase 0

**Feature Branch**: `[029-manifest-phase0]`  
**Created**: February 19, 2026  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 of the manifest task system as described in docs/ManifestTaskSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001 (ManifestTaskSystem §6.1, §6.1.1)**: Register a new Agent Queue job type named `manifest`, ensure the queue allowlist accepts it, and restrict execution to workers that advertise the `manifest` capability.
- **DOC-REQ-002 (ManifestTaskSystem §6.2, §6.2.1)**: Introduce a dedicated `manifest_contract` module that normalizes manifest job payloads, enforces `manifest.name` consistency with YAML metadata, and isolates validation from task-specific logic.
- **DOC-REQ-003 (ManifestTaskSystem §6.5)**: Server-side job creation must parse manifests to derive `requiredCapabilities` (manifest, embeddings, vector store, source adapters) rather than trusting client input.
- **DOC-REQ-004 (ManifestTaskSystem §6.6)**: Persist `manifestHash`, `manifestVersion`, and normalized `requiredCapabilities` inside each manifest queue payload for auditability.
- **DOC-REQ-005 (ManifestTaskSystem §7.1-§7.2)**: Expose minimal `/api/manifests` registry endpoints (GET/PUT) plus `/api/manifests/{name}/runs` to submit manifest jobs backed by stored YAML.

Each functional requirement below maps back to at least one DOC-REQ item to keep Phase 0 runtime scope aligned with the source contract.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit Manifest Job via Queue (Priority: P1)

An API engineer submits a manifest ingestion run through `/api/queue/jobs` using the new `manifest` type and expects the payload to be validated, normalized, and stored with derived capabilities so only manifest workers can execute it.

**Why this priority**: Without reliable queue plumbing, ingestion runs cannot be orchestrated or observed, blocking the entire manifest system.

**Independent Test**: Call the queue job creation endpoint with a valid v0 manifest payload and verify the persisted job shows `type=manifest`, normalized fields (hash, version, capabilities), and is claimable only by manifest workers.

**Acceptance Scenarios**:

1. **Given** a valid inline manifest payload, **When** the engineer POSTs `type="manifest"` to `/api/queue/jobs`, **Then** the API responds 201 with a job ID whose payload includes derived `requiredCapabilities` and stored `manifestHash`.
2. **Given** a payload where `manifest.name` differs from the manifest YAML metadata name, **When** the engineer tries to create the job, **Then** the API rejects it with a descriptive validation error before persisting.

---

### User Story 2 - Manage Manifests in Registry (Priority: P2)

A platform operator wants to store approved manifests in a registry and trigger runs without resending YAML each time.

**Why this priority**: Registry-backed runs enable governance and reuse, anchoring the queue payload contract in an authoritative source.

**Independent Test**: Use `PUT /api/manifests/{name}` to upsert YAML, `GET /api/manifests/{name}` to retrieve it, and `POST /api/manifests/{name}/runs` to create a queue job referencing the stored manifest while receiving the same normalization guarantees as inline submissions.

**Acceptance Scenarios**:

1. **Given** a new manifest definition, **When** the operator sends `PUT /api/manifests/weekly-confluence`, **Then** the registry stores the YAML, hash, version, and timestamps for future runs.
2. **Given** a stored manifest, **When** the operator calls `POST /api/manifests/weekly-confluence/runs` with `action="run"`, **Then** the API enqueues a `manifest` job using the registry content and returns the new job ID.

---

### User Story 3 - Observe Manifest Jobs Separately (Priority: P3)

A queue administrator filters the Tasks Dashboard or API responses by job type to track manifest ingestion progress independently of codex/gemini traffic.

**Why this priority**: Distinct job typing and capability derivation are prerequisites for UI separation and worker scheduling, even before UI work lands in later phases.

**Independent Test**: List queued jobs via `/api/queue/jobs?type=manifest` (or equivalent internal query) and confirm manifest jobs carry unique identifiers, events, and artifacts that distinguish them from other job types.

**Acceptance Scenarios**:

1. **Given** at least one manifest job exists, **When** the admin queries jobs filtered by `type=manifest`, **Then** only manifest jobs are returned with their derived metadata intact.

---

### Edge Cases

- Manifest payload `manifest.options` attempts to override structural fields (e.g., `vectorStore`); the API must reject with a specific error while allowing only `dryRun`, `forceFull`, or `maxDocs` overrides.
- A manifest references a data source adapter for which the server cannot derive a capability label; job creation must fail fast instead of enqueueing unclaimable work.
- Registry submission supplies YAML whose metadata name no longer matches the registry key; the system must prevent the mismatch until corrected.
- Queue jobs submitted before the manifest worker deploy expect legacy behavior; Phase 0 must ensure feature flags or migration paths avoid breaking existing task jobs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001)**: Update the Agent Queue job-type allowlist so `manifest` is accepted server-side, persisted alongside existing types, and claimable only by workers advertising the `manifest` capability flag.
- **FR-002 (DOC-REQ-002)**: Add `moonmind/workflows/agent_queue/manifest_contract.py` housing manifest-specific validation, normalization, and error types that are invoked whenever `type="manifest"` jobs are created.
- **FR-003 (DOC-REQ-002)**: Manifest normalization must parse the submitted YAML (inline or registry) and enforce that `payload.manifest.name` exactly matches `metadata.name` from the manifest body, rejecting mismatches before persistence.
- **FR-004 (DOC-REQ-003)**: Normalization must derive `requiredCapabilities` by inspecting manifest components (always `manifest`, plus `embeddings`, `qdrant`, and each data source adapter capability) while ignoring any client-supplied capability hints.
- **FR-005 (DOC-REQ-004)**: Persist `manifestHash`, `manifestVersion` (`"v0"` for compliant manifests), and the normalized `requiredCapabilities` into the queue job payload so downstream workers and dashboards can audit runs.
- **FR-006 (DOC-REQ-002 & DOC-REQ-003)**: Restrict `manifest.options` overrides to `dryRun`, `forceFull`, and `maxDocs`; attempts to mutate structural sections (sources, embeddings, vectorStore, security) must raise a validation error explaining the supported overrides.
- **FR-007 (DOC-REQ-005)**: Implement `GET /api/manifests` and `GET /api/manifests/{name}` to return stored manifests (YAML, metadata, hashes, timestamps) and `PUT /api/manifests/{name}` to upsert validated manifests after running the same normalization pipeline used for queue jobs.
- **FR-008 (DOC-REQ-005)**: Provide `POST /api/manifests/{name}/runs` that loads the stored manifest, injects it into a queue payload referencing `manifest.source.kind="registry"`, and then calls the queue service to create a `manifest` job with the same normalization outcomes as inline submissions.
- **FR-009 (DOC-REQ-001 & DOC-REQ-004)**: Ensure queue persistence, retrieval, and job listing APIs can filter or label manifest jobs distinctly (e.g., `type=manifest`) without leaking sensitive manifest content; only hashes, versions, and capability labels are exposed.
- **FR-010 (Runtime intent guard)**: Deliver automated unit tests (via `./tools/test_unit.sh`) covering manifest contract validation, capability derivation, registry CRUD, and job creation failure modes so Phase 0 ships with production runtime code plus validation tests.

### Key Entities *(include if feature involves data)*

- **ManifestJobPayload**: Normalized queue payload containing manifest YAML (inline or registry reference), derived `requiredCapabilities`, `manifestHash`, `manifestVersion`, allowable `options`, and audit metadata; consumed by the Agent Queue service and manifest workers.
- **ManifestRecord**: Registry persistence model storing manifest `name`, `content`, `content_hash`, `version`, timestamps, and optional last-run metadata to supply `registry`-kind runs.
- **ManifestCapabilitySet**: Derived list of capability tokens (e.g., `manifest`, `qdrant`, `embeddings`, `github`, `confluence`) computed per manifest and enforced during worker claim evaluation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of manifest job submissions (inline or registry) either succeed with normalized payloads that include `manifestHash`, `manifestVersion`, and complete capability sets or return actionable validation errors within 400 ms at the API layer.
- **SC-002**: Queue listing and worker-claim telemetry demonstrate that manifest jobs are only executed by workers advertising the `manifest` capability, with zero successful claims from non-manifest workers during automated tests.
- **SC-003**: Registry CRUD endpoints round-trip manifests with consistent hashes such that submitting the same YAML twice results in identical `content_hash` values and idempotent PUT responses verified by integration tests.
- **SC-004**: Unit and contract tests (run via `./tools/test_unit.sh`) cover at least the top five validation paths (name mismatch, unsupported option override, missing capability derivation, registry PUT invalid YAML, registry run creation), providing automated evidence that Phase 0 runtime code is production-ready.

## Assumptions & Constraints

- Phase 0 scope is limited to queue plumbing and registry APIs; manifest execution engines, workers, and UI surfaces are handled in later phases.
- Queue job payloads remain token-free by design; manifests must reference secrets via env/profile/vault tokens, but Phase 0 only validates that raw secrets are absent.
- Existing task job behavior must remain unchanged; new manifest logic is feature-flagged or isolated to the `manifest` job type paths.
- Runtime deliverables must include production code updates plus validation tests executed through `./tools/test_unit.sh`, satisfying the runtime intent guard stated in the task objective.

## Dependencies

- Agent Queue service modules responsible for job creation, persistence, and listing.
- Existing `moonmind/manifest` data models for schema validation (imported by the new manifest contract module).
- Authentication/authorization middleware already protecting `/api/queue` and `/api/manifests` endpoints.

