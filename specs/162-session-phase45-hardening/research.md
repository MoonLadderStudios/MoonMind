# Research: Codex Managed Session Phase 4/5 Hardening

## Decision 1: Keep Operator Visibility Bounded

**Decision**: Operator-facing workflow metadata, indexed fields, schedule metadata, activity summaries, and replay fixtures must contain only bounded identity, state, and artifact-reference values.

**Rationale**: Managed session prompts, transcripts, scrollback, raw logs, raw errors, and credentials are unbounded or sensitive. The existing managed-session architecture already treats artifacts plus bounded workflow metadata as the operator/audit surface, while the managed session store supports recovery and reconciliation.

**Alternatives considered**:

- Put rich session details in workflow metadata: rejected because indexed and display metadata can be exposed broadly and is not suitable for secrets or long provider output.
- Use container-local state for operator truth: rejected because container state is disposable and can be lost during cleanup or recovery.

## Decision 2: Preserve Runtime Activity Separation

**Decision**: Docker/container and managed runtime side effects must remain on the runtime activity boundary and must not move onto workflow-processing workers.

**Rationale**: Workflow workers should stay privilege-light and deterministic. Runtime side effects need retries, heartbeat/cancellation behavior where relevant, and worker topology that can be operated independently from workflow processing.

**Alternatives considered**:

- Run reconcile and cleanup directly in workflow code: rejected because workflow code must remain deterministic and side-effect free.
- Register heavy runtime activities everywhere for convenience: rejected because it weakens operational separation and can block workflow worker resources.

## Decision 3: Use Recurring Scheduled Reconcile for Recovery

**Decision**: A durable recurring reconcile/sweeper target should invoke managed-session reconciliation and return bounded outcomes for stale, degraded, missing, or orphaned state.

**Rationale**: Recurring recovery prevents leaked containers and stale supervision records without manual polling. Bounded outcomes keep operator visibility useful without exposing raw records or logs.

**Alternatives considered**:

- Manual reconcile only: rejected because unattended managed sessions need a durable recovery path.
- External cron outside Temporal: rejected because MoonMind already uses Temporal as the orchestration control plane for recurring operational work.

## Decision 4: Treat Lifecycle Integration Tests as Runtime Gates

**Decision**: Add integration coverage for the actual managed session workflow/update path and runtime activity boundary, with mocked runtime activities for deterministic local verification.

**Rationale**: Unit tests can validate helpers and direct workflow-object behavior, but workflow updates, query state, handler completion, and Continue-As-New interactions need a real Temporal worker execution path to catch durable-code regressions.

**Alternatives considered**:

- Unit tests only: rejected because they miss SDK method shapes, update handler lifecycle, and worker routing behavior.
- Live provider tests for required verification: rejected because required CI must remain credential-free; provider verification can remain optional.

## Decision 5: Require Replay Evidence for Workflow-Shape Changes

**Decision**: Workflow-definition changes that affect managed session handler shapes, state payloads, Continue-As-New input, or control semantics must include replay validation for representative histories or an explicit cutover explanation.

**Rationale**: Managed session workflows are durable code. Replay testing catches nondeterminism and history incompatibility before deployment.

**Alternatives considered**:

- Rely on new workflow executions only: rejected because already-running workflows can carry existing histories.
- Add compatibility aliases indefinitely: rejected by the pre-release constitution; obsolete internal contracts should be removed with explicit cutover or replay evidence.

## Decision 6: Verify Before Reimplementing Completed Behavior

**Decision**: Before adding runtime code, compare existing Phase 4/5 implementation against the spec and add only missing behavior or regression fixes.

**Rationale**: The feature explicitly says to implement only parts not fully implemented already. Verification-first work avoids duplicating completed schedule, visibility, worker, or reconcile surfaces.

**Alternatives considered**:

- Rebuild Phase 4/5 from the rollout text: rejected because it risks churn and regressions in already-passing behavior.
