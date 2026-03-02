# Research: Worker Self-Heal System (Phase 1)

## Decision 1: Phase-focused delivery contract

- **Decision**: Align this feature to a phased strategy and treat worker-side in-step recovery as the only implementation scope for now.
- **Rationale**: Runtime reliability issues are concentrated in step retry behavior; Phase 1 delivers immediate value without waiting for API/dashboard/schema expansion.
- **Alternatives Considered**:
  - Ship all hard-reset/operator controls together. Rejected due scope and risk.
  - Delay all work until operator APIs are ready. Rejected because current worker retry gaps are active production risk.

## Decision 2: Attempt loop integration lives in worker execution path

- **Decision**: Integrate self-heal into codex step execution via `_run_codex_step_with_self_heal` and keep non-codex runtimes unchanged.
- **Rationale**: Codex steps already run through a centralized execution path where cancel/pause/events/artifacts are managed.
- **Alternatives Considered**:
  - Global queue-level retry-only approach. Rejected because it cannot classify/repair failures in-step.

## Decision 3: Detection model = wall timeout + idle timeout + no-progress signatures

- **Decision**: Use `IdleTimeoutWatcher` pulses from output callbacks, wall timeout task race, and signature+diff repeat detection.
- **Rationale**: This is deterministic, low-overhead, and testable without upstream CLI changes.
- **Alternatives Considered**:
  - Process-level heartbeats from CLI. Rejected due cross-runtime contract churn.

## Decision 4: Queue retry escalation via structured self-heal run-quality reason

- **Decision**: On retryable exhaustion, return `run_quality_reason={category:self_heal, code:step_retryable_exhausted}` and mark failure retryable while queue attempts remain.
- **Rationale**: Reuses existing queue failure semantics and avoids new DB/API contracts.
- **Alternatives Considered**:
  - Directly enqueue a cloned job from worker. Rejected to keep ownership/lifecycle in queue service.

## Decision 5: Artifacts first, replay later

- **Decision**: Persist per-step and per-attempt state artifacts in Phase 1, but defer hard-reset replay activation.
- **Rationale**: Artifacts are immediately useful for observability and establish deterministic inputs for a later replay phase.
- **Alternatives Considered**:
  - Skip artifacts until hard reset is implemented. Rejected because telemetry/debugging would remain weak.

## Decision 6: Preserve and harden redaction behavior

- **Decision**: Route signatures/payloads through redaction and avoid over-redacting tiny dynamic values.
- **Rationale**: Prevents secret leakage while preserving useful operator diagnostics.
- **Alternatives Considered**:
  - Redact all dynamic values regardless of length. Rejected due false-positive masking in error messages.

## Deferred Questions (Phase 2/3)

- When hard reset is activated, should rebuild source use `startingBranch` only or allow a policy-defined override?
- Should operator `resume_from_step` require takeover mode?
- Which API payload shape should carry recovery commands with backward compatibility guarantees?
