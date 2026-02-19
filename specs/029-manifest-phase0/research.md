# Phase 0: Research Findings

**Feature**: Manifest Queue Phase 0  
**Branch**: `029-manifest-phase0`  
**Date**: February 19, 2026

## Manifest Payload Contract

### Decision
Use `moonmind/workflows/agent_queue/manifest_contract.py::normalize_manifest_job_payload` as the single entry point for validating and normalizing manifest queue submissions. The helper parses YAML with PyYAML, enforces `manifest.name == metadata.name`, restricts `manifest.options` to `dryRun | forceFull | maxDocs`, computes `manifestHash` (`sha256:` prefix), records `manifestVersion` (only `"v0"` accepted per §6.2), derives `requiredCapabilities`, and builds `effectiveRunConfig` by overlaying queue overrides on the manifest `run` block.

### Rationale
- Aligns directly with `docs/ManifestTaskSystem.md` §6.2–§6.6 requirements and keeps contract logic isolated from task-specific validation.
- Guarantees that both inline and registry submissions share the same normalization logic, preventing drift between `/api/queue/jobs` and `/api/manifests/{name}/runs`.
- Returning a normalized payload (instead of mutating in place) makes it easy to store sanitized payloads and to reuse the helper inside the registry service.

### Alternatives Considered
- Reusing the legacy `task_contract` module: rejected because task payload validation includes task-specific secrets/auth rules that should not apply to manifest jobs.
- Allowing clients to send pre-derived `requiredCapabilities`: rejected by DOC-REQ-003; derivation must happen server-side to prevent spoofing.

## Capability Derivation & Worker Enforcement

### Decision
`derive_required_capabilities()` will always include `manifest`, `embeddings`, the embeddings provider label (openai/google/ollama), the vector store capability (qdrant/pgvector/milvus), and one capability per data source adapter (GithubRepositoryReader → `github`, GoogleDrive → `gdrive`, Confluence → `confluence`, SimpleDirectory → `local_fs`). The Agent Queue repository already enforces capability superset checks inside `_is_job_claim_eligible`, so manifest workers must advertise at least `manifest,qdrant,embeddings,<provider>,<sources>`. Worker tokens issued for manifest workers will set `allowed_types=["manifest"]` plus the capabilities noted above.

### Rationale
- Ensures queue jobs are only claimable by workers with the declared hardware/data-plane access, satisfying DOC-REQ-001 and SC-002.
- Capability-derived filtering enables `/api/queue/jobs?type=manifest` to report progress separately from codex/gemini jobs (User Story 3).

### Alternatives Considered
- Encoding capabilities inside worker IDs rather than payload: rejected because queue filtering needs to know requirements before a worker claims the job.
- Allowing fallback to legacy job types: rejected; Phase 0 must distinctly separate manifest traffic from codex/gemini jobs.

## Registry Normalization & Hashing

### Decision
`api_service/services/manifests_service.py::upsert_manifest` will continue delegating to `normalize_manifest_job_payload` (with `action="plan"` and `source.kind="inline"`) to compute `content_hash` and `version` before persisting to the `manifest` table. Registry-backed run submissions load the stored YAML, embed it in a `source.kind="registry"` payload (content kept only long enough to hash/validate), and rely on the same normalization path before the queue job is created.

### Rationale
- Reusing the manifest contract guarantees consistent hash/version/computed options for both inline and registry runs (DOC-REQ-002/003/004/005).
- Persisting `content_hash` plus `version` alongside `last_run_*` metadata in `ManifestRecord` satisfies §7.1 expectations and lets `/api/manifests` report governance metadata without parsing YAML.

### Alternatives Considered
- Storing multiple versions per manifest name: deferred until later phases; Phase 0 only needs the latest YAML plus hash/metadata.
- Letting registry upsert skip validation for faster writes: rejected because manifests must be normalized before they can be queued or referenced.

## Queue Payload Sanitization

### Decision
Add a response-shaping helper (e.g., `sanitize_manifest_payload(payload: Mapping[str, Any])`) that strips `manifest.source.content`, omits any inline YAML, and surfaces only `manifest.name`, `action`, `source.kind`, optional `source.name`, `manifestHash`, `manifestVersion`, and `requiredCapabilities`. `/api/queue/jobs` and `/api/queue/jobs/{id}` serializers (`moonmind/schemas/agent_queue_models.JobModel`) will call this helper when `job.type == "manifest"` so UI/API consumers never receive raw manifest content while still being able to audit hashes and derived capabilities.

### Rationale
- Directly addresses FR-009 (no leakage of manifest YAML via queue listings) while preserving existing response shapes for other job types.
- Keeps sensitive payload redaction centralized, reducing the chance of future routes forgetting to strip inline YAML.

### Alternatives Considered
- Storing sanitized payloads in the database instead of response shaping: rejected because workers still need the full manifest content; sanitization should happen at serialization time.
- Using role-based checks to allow some users to see full manifests: out of scope for Phase 0 and unnecessary once hash-based auditing is exposed.

## Validation & Test Strategy

### Decision
Extend existing pytest suites (run through `./tools/test_unit.sh`) with:
- Contract tests for `normalize_manifest_job_payload` covering name mismatches, unsupported adapters, option validation, registry submissions, and capability derivation.
- Router/service tests for `/api/manifests` verifying 404/422 flows, content hash/version persistence, and run submission wiring to the queue service.
- Queue router serialization tests that assert manifest jobs expose only sanitized payload fields and respect `type=manifest` filtering.

### Rationale
- Provides automated evidence for DOC-REQ-001…005 plus FR-010 without waiting for integration tests.
- Builds on existing unit test structure (`tests/unit/workflows/agent_queue`, `tests/unit/api/routers`) so coverage is fast and localized.

### Alternatives Considered
- Relying solely on integration tests inside Docker Compose: rejected because runtime intent requires unit-coverage proof and integration runs are already handled in orchestrator pipelines.
