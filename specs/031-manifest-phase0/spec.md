# Feature Specification: Manifest Task System Phase 0

**Feature Branch**: `031-manifest-phase0`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 of the manifest task system as described in docs/ManifestTaskSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit Manifest Job via Queue (Priority: P1)

Platform engineers must be able to submit a manifest ingestion run through `/api/queue/jobs` so the Agent Queue can handle manifests exactly like other job types.

**Why this priority**: Without the submission path, no manifest run can be orchestrated or audited, blocking the remainder of the manifest system roadmap.

**Independent Test**: Issue a POST to `/api/queue/jobs` with `type="manifest"` and inline YAML; verify the API normalizes the payload, persists derived metadata, and the job becomes visible in queue listings without touching task-only code paths.

**Acceptance Scenarios**:

1. **Given** the manifest job type is registered, **When** a client submits a valid manifest payload with inline YAML, **Then** the queue stores the normalized payload including derived `requiredCapabilities`, hash, and manifest version.
2. **Given** the payload contains unsupported structural overrides, **When** the API validates the manifest options, **Then** it rejects the request with a descriptive 4xx error before persisting the job.

---

### User Story 2 - Manage Manifest Registry Entries (Priority: P2)

Operations teams need REST endpoints to store and retrieve manifest YAML, so they can reuse and govern manifests without resubmitting raw YAML each time.

**Why this priority**: Registry CRUD unlocks reproducibility and lets the submit UI choose from vetted manifests, which Phase 0 must support to keep data plane changes deterministic.

**Independent Test**: Call `PUT /api/manifests/{name}` with YAML to upsert, fetch it via `GET`, and submit a run with `POST /api/manifests/{name}/runs`; verify the queue job references the stored manifest and enforces naming rules.

**Acceptance Scenarios**:

1. **Given** a new manifest name, **When** an operator `PUT`s YAML with metadata.name matching the URL, **Then** the service stores the document with hash/version metadata and returns the persisted record.
2. **Given** an existing registry entry, **When** a run is triggered via `/runs`, **Then** the service creates a queue job referencing the registry content without exposing secrets and links the job id back to the manifest record.

---

### User Story 3 - Capability-Based Routing (Priority: P3)

Queue administrators need manifest jobs to advertise the correct `requiredCapabilities` so future manifest workers can safely claim only compatible jobs.

**Why this priority**: Accurate capability derivation is the linchpin for later phases; getting it right in Phase 0 prevents manifest jobs from being picked up by codex/gemini workers.

**Independent Test**: Provide manifests targeting different data sources and providers, submit jobs, and confirm the stored payload lists `manifest`, embeddings provider, vector store, and source capabilities; also verify jobs with missing capabilities are rejected.

**Acceptance Scenarios**:

1. **Given** a manifest referencing Confluence and Qdrant embeddings, **When** it is submitted, **Then** the derived required capabilities include `manifest`, `embeddings`, `confluence`, and `qdrant`.
2. **Given** a manifest payload missing embeddings configuration, **When** normalization runs, **Then** the API responds with a validation error describing the missing block rather than storing an incomplete job.

### Edge Cases

- Manifest payload name mismatch: reject when `payload.manifest.name` differs from `metadata.name` inside the YAML.
- Unsupported manifest action: only `plan` and `run` are accepted in Phase 0; requests with `evaluate` or unknown actions must fail fast.
- Secret leakage attempts: if inline YAML embeds a raw key or `manifest.options` tries to override structural sections, validation must fail without persisting the run.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Register `manifest` in the Agent Queue job type allowlist so queue validation, repositories, and API endpoints accept it without modifying codex task flows.
- **FR-002**: Introduce `moonmind/workflows/agent_queue/job_types.py` (or equivalent) that centralizes job type constants and is imported by queue services and workers.
- **FR-003**: Create `moonmind/workflows/agent_queue/manifest_contract.py` that validates manifest payloads, enforces naming consistency, and exposes `normalize_manifest_job_payload` plus `derive_required_capabilities`.
- **FR-004**: Update `AgentQueueService.create_job()` to route manifest jobs through the manifest contract while preserving existing behavior for `task`, `codex_exec`, and other types.
- **FR-005**: Derive and persist `payload.requiredCapabilities`, `manifestHash`, and `manifestVersion` for every manifest job regardless of what the client submits.
- **FR-006**: Enforce the options precedence rules: allow overriding `dryRun`, `forceFull`, and `maxDocs`, but reject attempts to change data sources, embeddings, or vector store structure through queue payload options.
- **FR-007**: Reject queue submissions that embed raw secrets; only env, profile, or vault references are allowed in manifest YAML according to `docs/ManifestTaskSystem.md`.
- **FR-008**: Provide `GET /api/manifests` and `GET /api/manifests/{name}` endpoints to list and retrieve stored manifests along with metadata such as hash, version, last run, and state timestamps.
- **FR-009**: Implement `PUT /api/manifests/{name}` to create or update manifests, validating YAML as v0 schema, storing hashes, and redacting secrets before persistence.
- **FR-010**: Implement `POST /api/manifests/{name}/runs` that loads the registry entry, normalizes queue payloads via FR-003, and links the resulting job id back to the manifest record.
- **FR-011**: Persist checkpoint-related columns (`version`, `updated_at`, `last_run_*`, `state_json`) on the manifest table even if checkpointing logic is filled later phases, so Phase 0 migrations unblock downstream work.
- **FR-012**: Update queue/event schemas so manifests appear as a distinct category in existing listings even before dedicated UI surfaces are shipped.
- **FR-013**: Deliver unit tests (via `./tools/test_unit.sh`) covering manifest contract normalization, capability derivation, registry CRUD, and queue submission failure modes to protect Phase 0 runtime behavior.
- **FR-014**: Enforce allowed `manifest.source.kind` values (`inline`, `registry`, and guarded `path` for dev/test images) when normalizing payloads so the worker always knows how to retrieve YAML while rejecting unsupported kinds per `docs/ManifestTaskSystem.md` §6.3.
- **FR-015**: Restrict manifest actions to `plan` and `run` during Phase 0 and surface descriptive validation errors for `evaluate` or unknown actions before persisting the queue job as mandated by §6.4.
- **FR-016**: Emit `payload.manifestSecretRefs` metadata that deduplicates profile and vault references from the manifest YAML so manifest workers can request credentials without embedding raw tokens, aligning with §6.6.

## Source Document Requirements

| ID | Source | Requirement | Functional Requirement Mapping |
|----|--------|-------------|--------------------------------|
| DOC-REQ-001 | §6.1 | Agent Queue must register `manifest` as a distinct job type that routes only to manifest-capable workers and surfaces as its own queue/event category. | FR-001, FR-002, FR-012 |
| DOC-REQ-002 | §6.2.1-§6.2.3 | Manifest submissions must use a dedicated contract module that validates YAML, enforces name consistency, and isolates options normalization from task jobs. | FR-003, FR-004, FR-006 |
| DOC-REQ-003 | §6.5-§6.6 | The API must derive `requiredCapabilities`, `manifestHash`, and `manifestVersion` for every manifest job regardless of client payload contents. | FR-005 |
| DOC-REQ-004 | §3.1 Goal 4 | Queue submissions must remain token-free, allowing only env/profile/vault references and rejecting raw secrets before persistence. | FR-007 |
| DOC-REQ-005 | §7.1-§7.2 | Provide manifest registry CRUD plus `/runs` submission that stores hashes, versions, checkpoint columns, and links job ids back to manifest metadata. | FR-008, FR-009, FR-010, FR-011 |
| DOC-REQ-006 | §6.3 | Manifest payloads must restrict `manifest.source.kind` to inline, registry, or guarded path support for dev/test while rejecting unsupported kinds. | FR-014 |
| DOC-REQ-007 | §6.4 | Manifest jobs may only request `plan` or `run` actions in Phase 0 and must fail fast for `evaluate` or unknown actions before persistence. | FR-015 |
| DOC-REQ-008 | §6.6 | API must emit `payload.manifestSecretRefs` metadata that catalogues profile and vault references so workers can resolve secrets safely without raw tokens. | FR-016 |

### Key Entities *(include if feature involves data)*

- **ManifestJobPayload**: Represents the normalized queue payload persisted for manifest jobs, including `manifest` YAML content or registry reference, `requiredCapabilities`, derived hash/version, and sanitized run options.
- **ManifestRecord**: Database row storing the authoritative manifest content, content hash, version, timestamps, last run metadata, and checkpoint `state_json` used later by the worker.
- **ManifestRunSubmission**: Logical construct linking a registry entry to a specific queue job, storing job id, manifest name, action (`plan` or `run`), and the derived run options snapshot for auditing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of POST `/api/queue/jobs` requests with `type="manifest"` either succeed with normalized payloads or return schema validation errors with actionable messages; no manifest payload bypasses the manifest contract.
- **SC-002**: Manifest registry endpoints can round-trip at least 50 sample manifests (inline + registry) with median request latency under 200 ms in local dev, proving readiness for UI consumers.
- **SC-003**: All manifest queue jobs persisted in Phase 0 automatically include correct capability lists, enabling a manifest worker prototype to claim them without code changes (<1% manual corrections during testing).
- **SC-004**: Unit tests covering the new contract, service layer, and API routers run via `./tools/test_unit.sh` and must fail if any validation rule or capability derivation is removed, ensuring runtime regressions are caught.

## Assumptions

- Phase 0 delivers control-plane plumbing only; the dedicated manifest worker and ingestion engine arrive in later phases, so downstream components may stub artifacts but must not block API delivery.
- Inline and registry manifest sources are required for this phase, guarded `path` references stay acceptable for dev/test environments, and `repo` sources remain future work until reader support expands.
- Existing Agent Queue authentication, artifact storage, and dashboard surfaces remain unchanged; Phase 0 simply extends them to recognize `manifest` job type entries.

## Dependencies & Risks

- **Queue Service Coupling**: Any refactor of `_SUPPORTED_QUEUE_JOB_TYPES` or `AgentQueueService` must ensure manifest handling does not regress task submission; unit tests from FR-013 mitigate this.
- **Schema Drift**: If `moonmind/schemas/manifest_models.py` changes while Phase 0 is in development, manifest contract validation must stay in sync; automated schema-loading tests should highlight drift.
- **Secret Handling**: Because payloads must stay token-free, regressions in secret-ref parsing could leak credentials; incorporate negative tests for raw secret detection before shipping Phase 0.
