# Research: Celery Chain Workflow Integration

## Broker and Result Backend Selection
- **Decision**: Use RabbitMQ 3.x as the Celery broker and PostgreSQL as the durable result backend while persisting long-lived workflow records in MoonMind-managed tables.
- **Rationale**: RabbitMQ provides reliable transport with acknowledgement semantics for the task chain, and PostgreSQL delivers durable, queryable storage so task states and artifacts survive worker restarts and broker restarts.
- **Alternatives considered**: Redis (familiar but lacks built-in acknowledgement semantics required for guaranteed delivery in this flow); RabbitMQ RPC result backend (simple but transient and unsuitable for restart survival); Dedicated result backend service (adds complexity without additional benefit beyond PostgreSQL).

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
