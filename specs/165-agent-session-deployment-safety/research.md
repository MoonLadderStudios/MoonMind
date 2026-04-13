# Research: Agent Session Deployment Safety

## Decision 1: Complete the feature by auditing and closing runtime gaps, not by reimplementing completed phases

**Decision**: Treat the current managed-session implementation as the starting point. Existing typed workflow updates, runtime steering and termination support, handler hardening, bounded visibility, scheduled reconcile, and worker deployment hooks must be verified against the spec and preserved where compliant.

**Rationale**: The feature request is to finish Phase 6 and remaining rollout work. Rebuilding surfaces that already exist would increase workflow-shape churn and replay risk without improving safety.

**Alternatives considered**:

- Reimplement the workflow from the document vocabulary: rejected because it would create unnecessary durable-code churn.
- Write a docs-only completion record: rejected by FR-001 and runtime mode.

## Decision 2: Production controls use typed Updates; Signals are restricted to fire-and-forget state propagation

**Decision**: The production mutation surface is the typed workflow update set: send turn, steer turn, interrupt turn, clear session, cancel session, and terminate session. Runtime handle attachment can remain a signal because it propagates state from launch/runtime setup rather than returning a control outcome.

**Rationale**: Operators and callers need request/response visibility for mutating session controls, and invalid mutations must be rejected deterministically before state changes.

**Alternatives considered**:

- Keep a generic `control_action` signal as the primary production mutator: rejected because it hides outcomes and cannot provide the same boundary validation semantics.
- Use queries for control feedback: rejected because queries must not mutate workflow state.

## Decision 3: Cancel and terminate remain distinct lifecycle actions

**Decision**: Cancel stops active work and leaves the session idle or recoverable. Terminate destroys/finalizes the runtime container path, supervision, session record, workflow state, and bounded operator metadata before the session is considered complete.

**Rationale**: Collapsing cancel and terminate would either leak runtime state or destroy sessions when an operator only intended to stop in-flight work.

**Alternatives considered**:

- Implement both as termination: rejected because it violates FR-008 and loses resumability.
- Implement both as best-effort workflow flags: rejected because it does not prove cleanup or supervision finalization.

## Decision 4: Side-effect boundaries must be idempotent and classify permanent failures

**Decision**: Launch, clear, interrupt, cancel, steer, and terminate behavior must be retry-safe at the activity/controller boundary, using stable request identity or current session/runtime state where available. Permanent invalid-runtime or unsupported-operation failures must be surfaced as explicit non-retryable application errors.

**Rationale**: Temporal activities are at-least-once. A control path that is correct only on first execution can leak containers, double-publish artifacts, or corrupt recovery records.

**Alternatives considered**:

- Rely on workflow locks only: rejected because activity retries can occur after workflow state serialized a request.
- Treat all failures as transient: rejected because permanent unsupported or invalid state would retry pointlessly and delay cleanup.

## Decision 5: Long-lived workflow safety uses locks, readiness gates, handler drain, and Continue-As-New from `run`

**Decision**: Async mutators that touch shared session state are serialized with workflow-safe locking, accepted runtime-bound controls wait for handles when appropriate, workflow completion or handoff waits for accepted handlers, and Continue-As-New is initiated from the main workflow path with bounded carry-forward state.

**Rationale**: Session workflows are message-heavy and can outlive individual turns. Handler races and unbounded histories are operational risks independent of individual control correctness.

**Alternatives considered**:

- Continue-As-New directly inside update handlers: rejected because the safe Temporal pattern is handoff from the main workflow path.
- Fail early-arriving controls before handles attach: rejected for accepted controls because launch races should be deterministic.

## Decision 6: Operator/audit truth, recovery index, and runtime cache are separate data planes

**Decision**: Artifacts plus bounded workflow metadata are the operator/audit truth. `ManagedSessionStore` records are the operational recovery and reconciliation index. Container-local state is disposable cache and must not be treated as durable truth.

**Rationale**: Operators need stable bounded refs, recovery needs a compact index, and runtime containers can disappear or be rebuilt.

**Alternatives considered**:

- Use the JSON session store as audit truth: rejected because it is an operational index and not the presentation/audit artifact system.
- Use container-local summary publication as the production artifact source: rejected because container-local helpers are fallback-only and may return empty refs.

## Decision 7: Observability surfaces expose bounded identifiers only

**Decision**: Workflow details, Search Attributes, activity summaries, schedule metadata, metrics, traces, logs, and replay fixtures may include bounded task run, runtime, session, epoch, status, degradation, and artifact-ref identifiers. They must exclude prompts, transcripts, scrollback, raw logs, credentials, secrets, and unbounded provider output.

**Rationale**: Indexed visibility and telemetry are broadly available operational surfaces. They need enough detail for correlation without copying sensitive or high-volume runtime content.

**Alternatives considered**:

- Include short prompt/log excerpts for convenience: rejected because excerpts can still contain secrets or sensitive user content.
- Store full continuity payloads in Search Attributes: rejected because visibility fields are for compact indexed values.

## Decision 8: Deployment safety is a rollout gate, not a best-effort checklist

**Decision**: Incompatible changes to workflow definition shape, handler names/signatures, persisted payloads, Continue-As-New state, or visibility fields require Worker Versioning, patching, or an explicit versioned cutover. Representative replay success and fault-injected lifecycle tests are deployment gates.

**Rationale**: Managed sessions are durable workflows. A code change that passes unit tests can still break open histories.

**Alternatives considered**:

- Depend only on unit tests: rejected because unit tests do not prove deterministic replay of existing histories.
- Leave bridge code indefinitely: rejected by the pre-release compatibility policy; any bridge must have cutover/removal conditions.

## Decision 9: Required validation is credential-free

**Decision**: Required tests must run without live external provider credentials. Provider verification remains optional/manual unless separately requested.

**Rationale**: Deployment safety gates must be enforceable in normal CI and managed-agent environments.

**Alternatives considered**:

- Use live provider sessions as required validation: rejected because credentials are not always available and would make merge safety non-deterministic.
