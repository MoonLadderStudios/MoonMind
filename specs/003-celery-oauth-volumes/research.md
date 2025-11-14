# Research Findings: Celery OAuth Volume Mounts

## Decision 1: Deterministic Codex Queue Sharding
- **Rationale**: Hash-based routing on a stable key (repo, project, or affinity token) ensures every Codex phase for a run reaches the same worker, preventing credential contention and aligning with the three-volume design from Spec Kit Option A.
- **Alternatives Considered**:
  - Round-robin routing: rejected because runs could bounce between workers, forcing multiple login states and risking token clobbering.
  - Manual queue selection per task: rejected due to operational overhead and higher risk of human error when routing jobs.

## Decision 2: Dedicated Auth Volumes Mounted at `$HOME/.codex`
- **Rationale**: Mapping each worker to a unique `codex_auth_{n}` named volume (0700 permissions) lets the ephemeral job container reuse persisted ChatGPT OAuth artifacts without storing tokens in images or environment variables.
- **Alternatives Considered**:
  - Shared network filesystem: rejected because it increases blast radius and complicates portability across hosts.
  - Re-authenticating per run with API keys: rejected because the Codex CLI flow relies on interactive ChatGPT login and would negate automation gains.

## Decision 3: Pre-flight `codex login status` Health Check
- **Rationale**: Running a short-lived container with the worker’s volume before launching Codex phases detects expired or missing credentials early, providing actionable remediation instructions and preventing half-complete runs.
- **Alternatives Considered**:
  - Relying on runtime failure from the job container: rejected because it wastes resources and hides the root cause in downstream task logs.
  - Periodic cron-based checks: rejected since Celery workers already control execution; integrating the check into the run lifecycle keeps context tight and avoids extra infrastructure.

## Decision 4: Operational Logging and Auditing
- **Rationale**: Capturing queue name, shard identifier, and volume label in Celery logs satisfies the spec’s audit requirements and supports tracing during incidents.
- **Alternatives Considered**:
  - Minimal logging limited to success/failure: rejected because it leaves operators without visibility into which volume served a run.
  - Centralized metrics only: rejected since metrics alone cannot provide per-run traceability required for compliance.

All identified clarifications have been resolved through existing Spec Kit guidance, so no open questions remain for `/speckit.clarify`.
