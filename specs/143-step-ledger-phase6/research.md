# Research: Step Ledger Phase 6

## Decision 1: Reconcile detail `runId` from the existing progress query instead of issuing a second latest-run query

- **Decision**: Reuse the already-required `get_progress` query to carry an internal latest-run identifier for router reconciliation, while keeping the external `ExecutionProgress` API shape unchanged.
- **Rationale**: Detail reads already pay for the bounded progress query. Reusing that query keeps detail polling cheap and avoids adding a second workflow roundtrip solely to repair stale projection `runId` values.
- **Alternatives considered**:
  - Query the full step ledger on every detail read. Rejected because it duplicates the later `/steps` fetch and inflates the detail read path.
  - Trust the projection row until it refreshes. Rejected because it leaves detail and step-ledger run identity inconsistent during Continue-As-New lag.

## Decision 2: Keep generic execution-wide artifacts keyed to the latest run once the Steps query resolves

- **Decision**: Mission Control should use the latest run exposed by the step ledger for generic execution-wide artifact reads whenever that latest-run data is available.
- **Rationale**: The page is explicitly latest-run-only. If the step ledger has already moved to the new run, the secondary Artifacts panel must not keep reading the old run's artifact namespace.
- **Alternatives considered**:
  - Keep using the initial execution-detail `runId`. Rejected because it can lag behind the workflow query during Continue-As-New rollover.

## Decision 3: Retire completed tmp rollout bullets rather than rewriting canonical docs

- **Decision**: Remove the step-ledger rollout bullets from the relevant `docs/tmp/remaining-work` files once Phase 6 tests pass.
- **Rationale**: Constitution Principle XII says completed migration/backlog notes should be removed rather than left behind after the work is done.
