# Research: Spec Kit Automation Pipeline

## Decision 1: Constitution Compliance Handling
- **Decision**: Treat the placeholder constitution as having no enforceable principles for this feature while documenting the gap and requesting governance updates.
- **Rationale**: The constitution file only contains template placeholders, so blocking delivery would provide no actionable guidance. Recording the gap keeps stakeholders informed and avoids inventing rules.
- **Alternatives Considered**:
  - Infer likely principles from prior specs – rejected because speculation could introduce conflicting requirements.
  - Pause planning until governance updates the constitution – rejected due to schedule pressure and the need to progress this automation feature.

## Decision 2: Container Orchestration Pattern
- **Decision**: Implement Docker-outside-of-Docker (DooD) where the Celery worker launches per-run job containers via the host Docker socket and mounts a shared `speckit_workspaces` volume.
- **Rationale**: Matches the design in `docs/SpecKitAutomation.md`, avoids DinD overhead, enables artifact sharing, and keeps the worker image slim.
- **Alternatives Considered**:
  - Docker-in-Docker – rejected due to performance penalties and isolation complexity.
  - Running Spec Kit directly inside the worker – rejected because it would bloat the worker image, complicate HOME isolation, and hinder future agent swaps.

## Decision 3: Secrets Injection Strategy
- **Decision**: Inject credentials (GitHub token, Codex API key, git identity) as environment variables when starting each job container; never persist them to files inside the shared volume.
- **Rationale**: Aligns with security guidelines in the design doc, keeps secrets ephemeral, and integrates cleanly with Docker CLI/SDK `environment` parameters.
- **Alternatives Considered**:
  - Mounting secret files into the container – rejected because shared volume retention risks credential leakage.
  - Storing secrets within Celery task payloads – rejected since it complicates logging and persistence surfaces.

## Decision 4: Artifact Retention Approach
- **Decision**: Persist per-phase logs, diff summaries, and commit status files in `/work/runs/{run_id}/artifacts` on the shared volume for at least seven days, with optional upload to external object storage.
- **Rationale**: Supports auditability and troubleshooting as required by the spec and leverages the existing volume without adding new infrastructure.
- **Alternatives Considered**:
  - Streaming artifacts directly to PostgreSQL – rejected because binary logs are poorly suited for relational storage.
  - Requiring external object storage from day one – rejected to keep initial deployment lightweight; integration can follow when needed.

## Decision 5: Observability & Metrics
- **Decision**: Expose structured logs with `{run_id, repo, phase, container_id, branch}` fields and emit optional StatsD metrics when `STATSD_HOST/PORT` or `SPEC_WORKFLOW_METRICS_*` environment variables are provided.
- **Rationale**: Mirrors the design doc’s recommendation, keeps logging consistent across Celery tasks, and allows incremental adoption of metrics without hard dependency.
- **Alternatives Considered**:
  - Embedding metrics directly into Celery result backend – rejected because numeric aggregation belongs in monitoring systems, not PostgreSQL.
  - Deferring metrics until after launch – rejected to avoid rework; keeping optional hooks now is low effort.
