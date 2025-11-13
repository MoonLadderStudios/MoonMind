# Research

## Scale & Capacity Planning
- Decision: Plan for up to 5 concurrent Spec workflow runs per MoonMind deployment while keeping RabbitMQ classic queues single-node.
- Rationale: Spec assumptions cite a single RabbitMQ node with default classic queues and no HA, which constrains safe concurrency; 5 chains keeps total Celery prefetch under typical 10-connection limits and leaves headroom for other workers.
- Alternatives considered: Scaling immediately to dozens of concurrent chains or enabling quorum queues, but that would require broker clustering work explicitly deferred in the spec.

## Celery 5.4 Chain Design Practices
- Decision: Compose the workflow with `chain` + `link_error` callbacks, use immutable signatures to prevent argument mutation, enable `acks_late` with retries, and persist chain IDs inside `SpecWorkflowRun`.
- Rationale: This matches Celery’s documented approach for deterministic sequencing and recovering after worker restarts; immutability avoids accidental parameter overrides when retrying tasks.
- Alternatives considered: Using `group` or `chord` primitives, but the workflow is strictly sequential so `chain` keeps state handling simpler.

## RabbitMQ Queue Configuration
- Decision: Keep a single durable `codex` queue with QoS prefetch=1 on Codex workers and default Celery queue for non-Codex steps; enable `reject_on_worker_lost` so stuck deliveries return to the queue.
- Rationale: Aligns with the new single-queue spec direction, preserves deterministic routing, and prevents multiple tasks from overwhelming limited Codex credentials.
- Alternatives considered: Continuing sharded `codex-{n}` queues, but the product decision is to consolidate onto one queue.

## PostgreSQL Result Backend Usage
- Decision: Continue using PostgreSQL for Celery backend plus dedicated tables `spec_workflow_runs` and `spec_workflow_task_states`, wrapping each task update in a transaction and storing artifacts under `var/artifacts/spec_workflows/<run_id>` with DB references.
- Rationale: Matches existing MoonMind persistence pattern and satisfies FR-007 traceability requirements without introducing new storage tech.
- Alternatives considered: Moving to Redis or S3 for artifacts, but that increases operational load and is outside the current scope.

## Codex CLI / Codex Cloud Authentication
- Decision: Enforce `codex login status` + `speckit --version` checks at worker startup and before submission tasks, using the persistent Codex auth volume mounted at `~/.codex` for every Codex phase.
- Rationale: Aligns with AGENTS.md guidance and FR-010 credential validation so Codex submissions fail fast with actionable errors.
- Alternatives considered: Lazy authentication inside each task, but that would reintroduce mid-run prompts and race conditions.

## GitHub API & Branch Operations
- Decision: Use existing GitHub CLI/REST helpers to create/update branches and PRs, derive branch names deterministically from the Spec identifier, and upsert PRs rather than opening duplicates.
- Rationale: Fulfills FR-005 & FR-008 idempotency requirements and keeps workflows compliant with current GitHub automation practices already in the repo.
- Alternatives considered: Direct git remote operations without PR automation, but that would forfeit observability and retry safety.

## FastAPI Orchestration Layer
- Decision: Extend `api_service/api/routers/workflows.py` + related services to enqueue Celery chains, expose run/status endpoints, and surface retries via authenticated HTTP actions.
- Rationale: This router already fronts workflow operations, so extending it preserves API consistency and reuses dependency injection/utilities.
- Alternatives considered: Adding a new service entrypoint or CLI command, but operators trigger runs via the MoonMind UI/API so FastAPI is the correct surface.

## API → Celery Integration Pattern
- Decision: Use application-level helper in `moonmind/workflows/speckit_celery` to build the Celery signature (chain) and enqueue via the shared Celery app, capturing the returned async result ID in `SpecWorkflowRun`.
- Rationale: Centralizes queue names, serialization, and auditing to a single module and makes retries/resumes reuse the same orchestration helper.
- Alternatives considered: Directly instantiating Celery tasks per API call, but that would duplicate routing logic and hinder retries.

## Celery ↔ Codex Container Execution
- Decision: Keep Codex steps inside the worker container but spawn the actual Spec Kit automation inside per-run Docker jobs (as described in docs), ensuring the Codex auth volume is mounted and log files stream back to `var/artifacts` for ingestion.
- Rationale: This preserves isolation between runs, allows reuse of the existing job container pattern, and makes it easier to collect JSONL logs required by FR-003/FR-004.
- Alternatives considered: Running Spec Kit inline inside the Celery worker process, but that would couple process lifecycles and complicate log streaming.

## Celery ↔ GitHub Integration Pattern
- Decision: Encapsulate GitHub interactions in dedicated utility functions/services that accept repository metadata from discovery outputs, authenticate via mounted credentials, and log responses for PR/audit updates.
- Rationale: Provides a clean seam for retries and meets FR-006 logging requirements since every request/response can be captured centrally.
- Alternatives considered: Embedding raw `gh` CLI calls throughout tasks, but that reduces observability and scatters credential handling.
