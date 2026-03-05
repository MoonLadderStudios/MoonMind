# Research: Workflow Type Catalog and Lifecycle

## Decision 1: Keep the v1 workflow type catalog fixed to two root types

- **Decision**: Use only `MoonMind.Run` and `MoonMind.ManifestIngest` as v1 root workflow categories.
- **Rationale**: Prevents taxonomy drift and keeps list/filter semantics stable for dashboard/API behavior.
- **Alternatives considered**:
  - Add additional root categories (`worker`, `orchestrator`, `task`): rejected because root categorization must be workflow type only.
  - Collapse ingest into run for v1: rejected because manifest orchestration behavior is materially distinct.

## Decision 2: Keep Workflow ID as the external identity and prefer Continue-As-New reruns

- **Decision**: Enforce `mm:<ulid-or-uuid>` Workflow IDs and preserve the same Workflow ID for rerun/restart via Continue-As-New by default.
- **Rationale**: Matches lifecycle identity expectations and supports stable API/dashboard references.
- **Alternatives considered**:
  - New Workflow ID per rerun: rejected because it fragments history and breaks default rerun contract expectations.
  - Encode metadata in Workflow ID: rejected due to security/privacy constraints.

## Decision 3: Use a single domain lifecycle state Search Attribute

- **Decision**: Keep `mm_state` as the single required domain-state Search Attribute with fixed values: `initializing|planning|executing|awaiting_external|finalizing|succeeded|failed|canceled`.
- **Rationale**: Provides consistent filtering and avoids multi-state ambiguity.
- **Alternatives considered**:
  - Multiple domain-state attributes: rejected because filtering semantics become ambiguous.
  - Derive UI state only from Temporal close status: rejected because non-terminal lifecycle phases must be visible.

## Decision 4: Keep Temporal close status mapping explicit and deterministic

- **Decision**: Map terminal lifecycle states as: Completed -> `succeeded`, Failed/TimedOut/Terminated -> `failed`, Canceled -> `canceled`.
- **Rationale**: Eliminates close-status interpretation drift between API, runtime service, and UI.
- **Alternatives considered**:
  - Preserve raw close status only: rejected because user-facing lifecycle contract requires normalized terminal states.
  - Map `terminated` to `canceled`: rejected because forced termination semantics are failure-class.

## Decision 5: Treat visibility and memo schema as contract surfaces

- **Decision**: Require indexed search fields `mm_owner_id`, `mm_state`, `mm_updated_at` and required memo fields `title`, `summary`; keep memo payloads bounded/human-readable.
- **Rationale**: Supports list/filter/detail behavior without large payload growth.
- **Alternatives considered**:
  - Store large prompt/manifest payloads in memo: rejected due to history/visibility bloat.
  - Skip `mm_updated_at`: rejected because sorting and recency behavior degrade.

## Decision 6: Use updates as the only edit semantics and enforce idempotency

- **Decision**: Keep `UpdateInputs`, `SetTitle`, and `RequestRerun` as explicit update contracts returning `{accepted, applied, message}`; support idempotency key replay.
- **Rationale**: Aligns edit behavior to Temporal Update semantics with predictable retry safety.
- **Alternatives considered**:
  - Use signals for user edits: rejected because edit operations require request/response contract semantics.
  - Best-effort updates without idempotency controls: rejected because duplicate client retries would produce inconsistent state.

## Decision 7: Use signals for asynchronous external/human events

- **Decision**: Keep `ExternalEvent` and `Approve` signal contracts, with optional `Pause`/`Resume`; validate required payload fields and handle asynchronously.
- **Rationale**: Matches async event delivery patterns and preserves workflow determinism.
- **Alternatives considered**:
  - Poll-only external integration: rejected due to higher latency and operational load.
  - Workflow-side authenticity verification logic: rejected because nondeterministic checks must remain in activities.

## Decision 8: Keep cancel vs forced termination semantics distinct

- **Decision**: Graceful user cancel transitions to `canceled`; forced termination transitions to failed-class semantics with reason captured in summary.
- **Rationale**: Makes operator intent and failure semantics explicit in lifecycle outputs.
- **Alternatives considered**:
  - Treat all stops as `canceled`: rejected because forced termination indicates failure semantics.
  - Attempt full cleanup on forced termination: rejected because ops termination prioritizes quick shutdown and bookkeeping.

## Decision 9: Use Continue-As-New thresholds to bound history growth

- **Decision**: Continue-As-New triggers are threshold-driven (step/wait-cycle counts by workflow type), preserve Workflow ID, and retain required artifact references/metadata.
- **Rationale**: Maintains replay performance and long-run stability.
- **Alternatives considered**:
  - No Continue-As-New policy: rejected due to unbounded history growth risk.
  - Continue-As-New only on manual rerun: rejected because long-lived workflows also require automated safeguards.

## Decision 10: Keep timeout/retry policy ownership with activity/runtime policy layers

- **Decision**: Enforce explicit timeout/retry defaults and callback-first monitoring with bounded polling/backoff fallback through runtime policy surfaces.
- **Rationale**: Prevents implicit reliability behavior and keeps failure semantics testable.
- **Alternatives considered**:
  - Ad hoc retries in business logic: rejected because policy behavior becomes inconsistent.
  - Callback-only monitoring: rejected because callback loss must degrade gracefully to bounded polling.

## Decision 11: Standardize failure taxonomy for UI-facing summaries

- **Decision**: Normalize failure categories to `user_error`, `integration_error`, `execution_error`, `system_error` with concise memo summary output.
- **Rationale**: Supports consistent operator/debugging outcomes across workflow types.
- **Alternatives considered**:
  - Raw exception names in UI metadata: rejected as unstable and implementation-leaky.
  - Single generic failure category: rejected because triage quality degrades.

## Decision 12: Enforce owner/admin control-plane authorization at API + runtime boundaries

- **Decision**: Require owner/admin authorization for update/signal/cancel/rerun at API entry and preserve defense-in-depth invariants in runtime service/workflow boundaries.
- **Rationale**: Aligns control-plane security with documented invariants and prevents bypass paths.
- **Alternatives considered**:
  - API-only authorization: rejected because defense-in-depth requires boundary revalidation.
  - Role-blind workflow controls: rejected due to unauthorized state mutation risk.

## Decision 13: Runtime mode is the completion gate for this feature

- **Decision**: Keep orchestration in runtime implementation mode: code and tests are mandatory deliverables.
- **Rationale**: User objective and FR-021 explicitly reject docs-only completion.
- **Alternatives considered**:
  - Docs-only closure: rejected as non-compliant with required deliverables.
  - Runtime changes without repository-standard unit validation: rejected because `./tools/test_unit.sh` is required for acceptance.
