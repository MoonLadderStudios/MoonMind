# Research: Integrations Monitoring Design

## Decision 1: Keep `MoonMind.Run` as the default orchestration anchor

- **Decision**: Implement external monitoring inside the existing `MoonMind.Run` lifecycle and extend the current `TemporalExecutionService` projection/state machine rather than adding a new provider root workflow.
- **Rationale**: Directly satisfies `DOC-REQ-001` and `DOC-REQ-002`, keeps lifecycle state unified, and matches the repo's current Temporal execution model.
- **Alternatives considered**:
  - Add one root workflow type per provider: rejected because it violates the source design and would fragment lifecycle visibility.
  - Introduce a separate product-level integration queue: rejected because task queues are worker-routing concepts, not product semantics.

## Decision 2: Provider specifics stay behind shared activity contracts

- **Decision**: Standardize provider behavior around `integration.<provider>.start`, `status`, `fetch_result`, and `cancel`, with `mm.activity.integrations` as the default routing target.
- **Rationale**: Meets `DOC-REQ-005`, `DOC-REQ-006`, and `DOC-REQ-011` while preserving provider neutrality and minimal worker topology.
- **Alternatives considered**:
  - Embed provider-specific branches directly in workflow state transitions: rejected because it hard-codes vendor behavior into core lifecycle logic.
  - Split provider-specific task queues immediately: rejected because the design says to start shared and split only when secrets/quotas/isolation require it.

## Decision 3: Reuse the existing Jules adapter for the first provider profile

- **Decision**: Build the first provider profile on top of `moonmind/workflows/adapters/jules_client.py` and normalize Jules task states into the shared status vocabulary.
- **Rationale**: Satisfies `DOC-REQ-015`, keeps Jules-specific HTTP details behind an existing adapter seam, and avoids duplicate integration logic.
- **Alternatives considered**:
  - Write a new Jules-only monitoring client inside Temporal service code: rejected because it duplicates transport logic and weakens modularity.
  - Delay Jules until after generic plumbing: rejected because the source design explicitly requires Jules as the first delivered provider profile.

## Decision 4: Durable correlation records are the primary callback lookup mechanism

- **Decision**: Use `temporal_integration_correlations` as the durable lookup table for callback routing and Continue-As-New survivability, keyed by provider callback material and stable correlation identity.
- **Rationale**: Implements `DOC-REQ-008`, `DOC-REQ-009`, and `DOC-REQ-011`, and fits the migration already started in `202603060001_integrations_monitoring.py`.
- **Alternatives considered**:
  - Resolve callbacks by searching visibility for external operation IDs: rejected because it is explicitly discouraged and fails durability/performance expectations.
  - Keep callback correlation only in workflow history: rejected because `run_id` changes across Continue-As-New and workflow history is the wrong lookup surface for API ingress.

## Decision 5: Hybrid callback-plus-polling is the default monitoring mode

- **Decision**: Treat callbacks as preferred and polling as safety-net behavior, with terminal-state latching and bounded duplicate-event handling in workflow-side state.
- **Rationale**: Satisfies `DOC-REQ-007`, `DOC-REQ-009`, and `DOC-REQ-014`, and matches the documented default operating posture.
- **Alternatives considered**:
  - Callback-only monitoring: rejected because providers can miss or delay callbacks.
  - Poll-only monitoring: rejected because it increases latency, cost, and rate-limit pressure and ignores available callback support.

## Decision 6: Poll scheduling is configurable and provider-aware

- **Decision**: Accept provider-recommended poll intervals when reasonable, otherwise start near 5 seconds, apply bounded backoff with jitter, and reset cadence on meaningful status changes.
- **Rationale**: Directly covers `DOC-REQ-011` and supports safe operation under provider quotas and long waits.
- **Alternatives considered**:
  - Fixed polling interval for all providers: rejected because it ignores provider guidance and wastes capacity.
  - Store complex unbounded poll history in workflow state: rejected because workflow history must stay compact and deterministic.

## Decision 7: Compact monitoring state lives in workflow state; payloads live in artifacts

- **Decision**: Keep only bounded monitoring fields in `integration_state`, and store raw callbacks, status dumps, outputs, and failure diagnostics as Temporal artifacts.
- **Rationale**: Satisfies `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-010`, and `DOC-REQ-013` while reusing the existing Temporal artifact system.
- **Alternatives considered**:
  - Put raw provider payloads in memo or search attributes: rejected because it leaks data and bloats visibility state.
  - Persist all provider payloads directly in `integration_state`: rejected because it violates workflow-history discipline and redaction boundaries.

## Decision 8: Continue-As-New must preserve monitoring identity, not provider history

- **Decision**: Continue-As-New will preserve `workflow_id`, `correlation_id`, callback routing, and bounded active monitoring state, while refreshing `run_id` and clearing transient counters.
- **Rationale**: Required by `DOC-REQ-011` and already consistent with the current `TemporalExecutionService` Continue-As-New pattern.
- **Alternatives considered**:
  - Keep the same `run_id` forever: rejected because Continue-As-New semantics require a new run execution.
  - Rebuild monitoring state from provider scans after Continue-As-New: rejected because it adds non-deterministic recovery logic and weakens idempotency.

## Decision 9: Cancellation semantics must stay explicit and provider-aware

- **Decision**: User cancellation will attempt provider cancellation when supported, but the system will explicitly surface unsupported, ambiguous, or best-effort results instead of reporting false success.
- **Rationale**: Implements `DOC-REQ-006` and `DOC-REQ-012`, and keeps operator-facing outcomes honest.
- **Alternatives considered**:
  - Always report cancellation success locally once requested: rejected because it hides provider truth and violates the source design.
  - Refuse user cancellation unless provider cancellation exists: rejected because MoonMind cancellation semantics still need to close the run even when provider cancellation is unavailable.

## Decision 10: Callback verification belongs in the API layer, before workflow mutation

- **Decision**: Signature/auth checks, size limits, schema validation, and optional raw callback artifact capture happen in API/ingress code before signaling the workflow.
- **Rationale**: Satisfies `DOC-REQ-003`, `DOC-REQ-008`, and `DOC-REQ-013`, and preserves deterministic workflow code.
- **Alternatives considered**:
  - Verify callbacks inside workflow logic: rejected because network and security side effects do not belong in workflow code.
  - Accept all callbacks and let the workflow discard invalid ones later: rejected because it creates avoidable security and noise risks.

## Decision 11: Visibility and memo remain compact and operator-readable

- **Decision**: Keep canonical `mm_*` search attributes, add only bounded integration-specific fields like `mm_integration`, and limit memo to summary/title/safe display details such as `external_url`.
- **Rationale**: Implements `DOC-REQ-010` and preserves clean dashboard/API behavior.
- **Alternatives considered**:
  - Index provider event IDs or external operation IDs by default: rejected because they are high-cardinality and explicitly discouraged.
  - Put detailed provider state in memo: rejected because memo should remain human-readable and compact.

## Decision 12: Runtime-vs-docs behavior stays aligned to runtime mode

- **Decision**: Keep runtime mode as the selected orchestration mode for this feature, requiring production code changes and validation tests; document docs-mode scope-check behavior only for consistency.
- **Rationale**: Satisfies `FR-029`, `FR-030`, and the user objective, and prevents false completion through documentation-only edits.
- **Alternatives considered**:
  - Treat planning/docs artifacts as sufficient completion: rejected because the feature explicitly requires runtime implementation plus tests.
  - Ignore mode semantics in planning artifacts: rejected because downstream task generation would drift from the selected runtime mode.
