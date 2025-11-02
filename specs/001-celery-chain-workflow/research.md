# Research: Celery Chain Workflow Integration

## Broker and Result Backend Selection
- **Decision**: Use Redis 8 as the Celery broker with PostgreSQL as the result backend via `django-celery-results`-style tables implemented directly in MoonMind.
- **Rationale**: Redis already appears in MoonMind deployment options, offers low-latency pub/sub, and simplifies horizontal scaling. Persisting results in PostgreSQL keeps long-lived workflow metadata close to the API, enables transactional updates, and survives broker pruning.
- **Alternatives considered**: RabbitMQ (robust but adds new infrastructure management); Redis for both broker and results (simpler but complicates historical queries and retention policies).

## Task Retry and Resume Strategy
- **Decision**: Persist task progress after each Celery stage and implement idempotent tasks that can resume from stored artifacts when retries trigger.
- **Rationale**: Aligns with requirement to restart from failed stage, reduces re-execution of earlier steps, and supports operator-triggered retries without risking duplicate PRs/branches.
- **Alternatives considered**: Full chain restart (simpler but violates requirement to resume from failure); Manual compensating actions (slow, operator-heavy).

## Artifact Storage and Surfacing
- **Decision**: Store Codex JSONL logs and Git patches on the filesystem under `var/artifacts/spec_workflows/<run_id>/` and reference them from workflow records.
- **Rationale**: Keeps large artifacts out of the database while remaining accessible to the API and UI; matches existing pattern for repo summaries and avoids immediate need for object storage.
- **Alternatives considered**: Database BLOB storage (increases DB size and complicates backups); External object storage (future-friendly but introduces new secrets and deployment work for this iteration).

## Secrets Management Approach
- **Decision**: Leverage MoonMind's existing secret provider abstraction to mount Codex and GitHub credentials for the Celery worker at runtime, validating them during the pre-flight task.
- **Rationale**: Reuses audited secret handling, supports rotations, and matches requirement to fail fast when credentials expire.
- **Alternatives considered**: Embedding secrets in Celery config (hard to rotate); Per-run manual input (defeats automation and increases operator burden).
