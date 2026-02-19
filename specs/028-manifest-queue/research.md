# Research: Manifest Queue Plumbing (Phase 0)

## Decision 1: Shared Job Type Registry Module
- **Decision**: Introduce `moonmind/workflows/agent_queue/job_types.py` to host `SUPPORTED_QUEUE_JOB_TYPES = {"task", "codex_exec", "codex_skill", "manifest"}` and import it from `service.py` plus future workers.
- **Rationale**: Centralizing job types prevents manifest support from being entangled with task-specific constants and simplifies future additions (e.g., UI filtering) by allowing a single source of truth.
- **Alternatives considered**: (a) Update the existing inline `_SUPPORTED_QUEUE_JOB_TYPES` constant in `service.py` — rejected because multiple modules currently duplicate the allowlist and manifest support would remain brittle; (b) store job types in config or database — rejected for Phase 0 because job types rarely change and config indirection would slow startup while offering no gain.

## Decision 2: Manifest Contract Module Responsibilities
- **Decision**: Build a manifest contract module that (a) parses YAML using existing manifest loaders, (b) enforces `payload.manifest.name == metadata.name`, (c) computes `manifestHash` + `manifestVersion`, (d) derives `requiredCapabilities` by inspecting embeddings/vector store/data sources, and (e) strips/validates that only env/profile/vault refs appear in payloads.
- **Rationale**: Aligns directly with docs/ManifestTaskSystem Section 6, keeps queue normalization deterministic, and isolates manifest rules from task contract logic so we avoid regressing codex job submissions.
- **Alternatives considered**: (a) Extend `task_contract.py` with manifest paths — rejected because that file was purpose-built for task payloads and mixes many assumptions (vault auth, runtime selection) that do not apply to manifests; (b) rely on worker-side validation only — rejected since API must block invalid payloads before they reach the queue.

## Decision 3: Registry API Layering
- **Decision**: Create a lightweight `ManifestsService` inside `api_service/services` that wraps SQLAlchemy/Repository calls and exposes CRUD helpers plus `submit_run()` which internally calls `AgentQueueService.create_job(type="manifest", payload=...)` using registry YAML.
- **Rationale**: Keeps FastAPI routers thin, reuses queue service for job creation, and provides a seam for caching/hash comparisons or metrics later without modifying controllers directly.
- **Alternatives considered**: (a) Embed DB/queue calls straight inside the FastAPI router functions — rejected for testability and maintainability; (b) use Celery or asynchronous background tasks to submit runs — rejected because queue submissions must remain synchronous so the caller gets a job id immediately.

## Decision 4: Hashing + Options Precedence
- **Decision**: Hash manifest YAML + normalized source metadata using SHA-256 (matching existing manifest code), store hash + version in payload + DB record, and merge `manifest.run` with queue-supplied overrides limited to `dryRun`, `forceFull`, `maxDocs`.
- **Rationale**: Deterministic hashing lets workers spot registry drift; restricting overrides to run-control keys prevents clients from mutating structural manifest content which could cause drift or security regressions.
- **Alternatives considered**: (a) Accept arbitrary overrides from the queue payload — rejected for safety; (b) drop hashing and rely on DB timestamps — rejected because doc explicitly calls for `manifestHash` and hash-based change detection for idempotency.
