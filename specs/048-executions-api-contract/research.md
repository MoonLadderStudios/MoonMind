# Research: Executions API Contract Runtime Delivery

## Decision 1: Keep `/api/executions` as a stable adapter contract over the current projection-backed implementation

- **Decision**: Treat `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` as the contract boundary while allowing the backing read/write path to remain `TemporalExecutionRecord`-based for now.
- **Rationale**: The source doc explicitly says the public execution contract must survive future backend changes. Freezing the adapter surface instead of the storage shape preserves migration flexibility.
- **Alternatives considered**:
  - Expose projection-row details directly as the public contract. Rejected because the doc requires implementation independence.
  - Delay the contract until direct Temporal Visibility is ready. Rejected because callers already need a stable execution API now.

## Decision 2: `workflowId` is the only durable identity on `/api/executions`

- **Decision**: Keep `workflowId` as the canonical durable execution identifier, keep `runId` as current-run detail only, and forbid `taskId` from the direct execution API payloads.
- **Rationale**: This is the central migration invariant for execution-shaped APIs and avoids clients binding to unstable run identifiers.
- **Alternatives considered**:
  - Let clients address executions by `runId`. Rejected because reruns and Continue-As-New rotate `runId`.
  - Return both `workflowId` and `taskId` on `/api/executions`. Rejected because the source contract explicitly excludes `taskId` from this API.

## Decision 3: Preserve `taskId == workflowId` only in compatibility adapters

- **Decision**: Keep task-facing or dashboard-facing compatibility code responsible for the fixed bridge `taskId == workflowId` during migration.
- **Rationale**: This preserves existing task-oriented consumers without contaminating the execution-oriented API contract.
- **Alternatives considered**:
  - Remove the compatibility bridge immediately. Rejected because the migration posture keeps task-oriented surfaces alive.
  - Implement the bridge inside `/api/executions` responses. Rejected because it breaks the documented execution adapter posture.

## Decision 4: Keep list pagination opaque even if it stays offset-based internally

- **Decision**: Continue using the current encoded offset token internally if desired, but keep `nextPageToken` contractually opaque and always return `countMode` explicitly.
- **Rationale**: Opaque cursor semantics let the backend evolve later without breaking clients, while `countMode` guards against false assumptions about count precision.
- **Alternatives considered**:
  - Document the token as an offset. Rejected because the source doc forbids clients from depending on internal pagination structure.
  - Omit `countMode` because counts are exact today. Rejected because future implementations may not preserve exact counts.

## Decision 5: Keep owner/admin enforcement and non-disclosing direct access at the router boundary

- **Decision**: Router handlers remain responsible for authenticated user resolution, admin scope checks, `403 execution_forbidden` on invalid list scope, and non-disclosing `404 execution_not_found` on direct fetch/control paths.
- **Rationale**: The router already owns HTTP semantics and can reliably enforce the difference between list-scope denial and hidden-resource denial.
- **Alternatives considered**:
  - Push all authorization behavior down into the service. Rejected because the service does not own HTTP status-code semantics.
  - Return `403` for hidden direct operations. Rejected because the contract intentionally avoids existence disclosure.

## Decision 6: Keep create idempotency and update idempotency intentionally narrow

- **Decision**: Preserve create deduplication on `(ownerId, workflowType, idempotencyKey)` and preserve update replay only for the most recent matching update idempotency key.
- **Rationale**: Narrow idempotency is already documented and keeps storage/state bookkeeping simple and explicit.
- **Alternatives considered**:
  - Add a full historical idempotency ledger. Rejected because it is more complex than the contract requires.
  - Remove idempotency handling from updates. Rejected because client retries would become unsafe.

## Decision 7: Continue-As-New remains the runtime model for rerun and major update rollovers

- **Decision**: Keep `RequestRerun` and some update-triggered lifecycle rollovers as same-`workflowId`, new-`runId` transitions.
- **Rationale**: This matches the current runtime behavior and the documented migration semantics around durable execution identity.
- **Alternatives considered**:
  - Allocate a new `workflowId` per rerun. Rejected because it fragments logical execution history.
  - Hide `runId` entirely. Rejected because the source doc still allows it as debug/detail data.

## Decision 8: Baseline search attributes and memo keys are required, but both objects stay extensible

- **Decision**: Require `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry` plus `memo.title` and `memo.summary`, while allowing additive keys and preserving opaque-object semantics for clients.
- **Rationale**: Clients need stable baseline fields, but future metadata additions should not require breaking changes.
- **Alternatives considered**:
  - Freeze the full object shape with no additive keys. Rejected because the doc explicitly allows extensibility.
  - Treat memo and search attributes as best-effort only. Rejected because several contract requirements depend on these baseline keys.

## Decision 9: Structured domain errors stay stable even while framework validation errors remain outside the domain contract

- **Decision**: Keep router-raised domain errors in the `detail.code` / `detail.message` shape and tolerate FastAPI/Pydantic validation errors as separate framework behavior.
- **Rationale**: This preserves a stable API contract without fighting the framework's pre-route validation model.
- **Alternatives considered**:
  - Wrap every framework validation error into the domain envelope. Rejected because it is brittle and not required by the source doc.
  - Use plain strings for domain errors. Rejected because the doc defines a structured shape.

## Decision 10: Runtime mode is mandatory for this feature package

- **Decision**: Keep the orchestration mode in runtime implementation mode and require production code changes plus automated validation tests as the feature completion gate.
- **Rationale**: The task objective and `DOC-REQ-015` both reject docs/spec-only completion.
- **Alternatives considered**:
  - Treat this as a documentation-only alignment task. Rejected as non-compliant with the feature scope.
  - Accept manual-only validation. Rejected because the spec requires automated validation coverage.
