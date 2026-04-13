# Research: Temporal Compose Foundation

## Decision 1: Keep Docker Compose as the canonical self-hosted Temporal deployment path

- **Decision**: Use `docker-compose.yaml` Temporal services (`temporal-db`, `temporal`, `temporal-namespace-init`) as the required deployment baseline and evolve them with hardening scripts rather than introducing a parallel runtime.
- **Rationale**: Aligns directly with `DOC-REQ-001`, preserves one-click operator path, and minimizes deployment divergence.
- **Alternatives considered**:
  - Kubernetes/Helm-first rollout: rejected because this feature explicitly targets Docker Compose foundation.
  - External Temporal Cloud dependency: rejected by locked self-hosted decision.

## Decision 2: Treat PostgreSQL SQL visibility as a first-class upgrade-gated dependency

- **Decision**: Keep PostgreSQL as both persistence and advanced visibility backend, and add explicit schema upgrade rehearsal as a rollout blocker.
- **Rationale**: Satisfies `DOC-REQ-002` and `DOC-REQ-003` while preventing silent visibility regressions during server upgrades.
- **Alternatives considered**:
  - Persistence-only Postgres + alternate visibility backend: rejected due to source doc requirements and extra operational complexity.
  - Best-effort visibility upgrades without rehearsal: rejected because rollout safety requires deterministic gating.

## Decision 3: Keep namespace retention management explicit, idempotent, and storage-cap governed

- **Decision**: Use namespace reconciliation automation with defaults `TEMPORAL_NAMESPACE=moonmind`, `TEMPORAL_RETENTION_MAX_STORAGE_GB=100`, and explicit retention updates on each bootstrap.
- **Rationale**: Aligns with `DOC-REQ-004`, avoids hidden namespace defaults, and supports troubleshooting-first retention intent.
- **Alternatives considered**:
  - One-time namespace setup only: rejected because drift/redeploy scenarios need idempotent reconciliation.
  - Pure time-based retention policy: rejected because policy is storage-cap driven, not compliance-timed.

## Decision 4: Make Temporal Visibility the sole list-of-record for execution views

- **Decision**: Implement execution list/filter/pagination/count from Temporal Visibility APIs with search attributes and page tokens; keep local DB only for supplemental metadata.
- **Rationale**: Directly enforces `DOC-REQ-005` and avoids cross-source pager inconsistency.
- **Alternatives considered**:
  - Merged DB + Temporal list models: rejected due to correctness and pagination-token mismatch risks.
  - DB materialization as primary list source: rejected because architecture mandates Temporal-native visibility truth.

## Decision 5: Encode task queues as routing boundaries only

- **Decision**: Use stable queue taxonomy (`mm.workflow`, `mm.activity.*`) and forbid product-level ordering/queue semantics in API/UI contracts.
- **Rationale**: Required by `DOC-REQ-006`; keeps Temporal abstraction boundaries clean.
- **Alternatives considered**:
  - Expose queue names/order semantics to end users: rejected as architectural drift.
  - Single queue for all workloads: rejected due to isolation/secrets/scaling needs.

## Decision 6: Use direct task-queue polling without Worker Deployment routing

- **Decision**: Workers poll their configured workflow and activity task queues directly and do not require Temporal Worker Deployment current-version state.
- **Rationale**: Meets `DOC-REQ-007` while preserving one-click local operation and avoiding deployment-version routing drift.
- **Alternatives considered**:
  - Worker Deployment routing: rejected because it adds an operator-controlled current-version state that can strand new tasks on unpolled queues.
  - Implicit per-worker version behavior: rejected because worker routing must be observable and task-queue based.

## Decision 7: Keep shard-count choice as a pre-rollout gate, not an implicit default

- **Decision**: Retain `TEMPORAL_NUM_HISTORY_SHARDS` (default `1`) but require a signed decision record and migration-impact acknowledgement in rollout checks.
- **Rationale**: Satisfies `DOC-REQ-008` and prevents accidental lock-in through undocumented defaults.
- **Alternatives considered**:
  - Hide shard count from operator controls: rejected because irreversibility requires explicit ownership.
  - Set high shard count preemptively without decision gate: rejected as unnecessary complexity for early scope.

## Decision 8: Replace external recurring scheduler ownership with Temporal Schedules

- **Decision**: Migrate recurring trigger orchestration to Temporal Schedules; keep any remaining scheduler code only as migration shims.
- **Rationale**: Implements `DOC-REQ-009` and consolidates periodic behavior into one workflow engine.
- **Alternatives considered**:
  - Keep cron/beat/custom scheduler as primary: rejected by platform contract.
  - Hybrid ownership long-term: rejected because it creates dual-source-of-truth behavior.

## Decision 9: Use Temporal-native lifecycle contracts and keep side effects in activities

- **Decision**: Introduce lifecycle APIs (`start`, `update/signal`, `cancel`, `list`, `describe`) backed by Temporal execution semantics and activity boundaries.
- **Rationale**: Meets `DOC-REQ-011`, `DOC-REQ-012`, and preserves deterministic workflow code.
- **Alternatives considered**:
  - Keep Celery workflow execution ownership: rejected by Temporal-first architecture.
  - Direct side effects in workflow orchestration code: rejected due to nondeterminism.

## Decision 10: Keep large payloads/logs as artifact references, and enforce manifest failure policy contract

- **Decision**: Persist large payloads/logs outside workflow history and model manifest execution policy as explicit enum input (`fail_fast`, `continue_and_report`, `best_effort`).
- **Rationale**: Covers `DOC-REQ-013` and `DOC-REQ-014` while keeping history manageable and behavior explicit.
- **Alternatives considered**:
  - Inline large payloads into workflow state/history: rejected due to history growth and replay costs.
  - Implicit/default manifest failure behavior: rejected because policy must be operator-visible and deterministic.

## Decision 11: Model external monitoring interactions through callback/signal with timer polling fallback

- **Decision**: Handle long-lived external interactions with signal-driven callbacks and Temporal timer-based polling fallback patterns.
- **Rationale**: Implements `DOC-REQ-015` and aligns with Temporal-native resilience.
- **Alternatives considered**:
  - External ad hoc polling daemon: rejected because it bypasses workflow durability/state.
  - Callback-only with no fallback: rejected because callback delivery can fail.

## Decision 12: Runtime-vs-docs mode alignment is a hard gate for this feature

- **Decision**: Treat this feature as runtime implementation scope end-to-end; planning/tasks must include production code + validation tests.
- **Rationale**: User objective and spec explicitly require runtime deliverables, not docs-only output.
- **Alternatives considered**:
  - Docs/spec-only completion: rejected as out of scope and non-compliant with acceptance criteria.
  - Partial validation without repository-standard test command: rejected; `./tools/test_unit.sh` remains required for unit acceptance.
