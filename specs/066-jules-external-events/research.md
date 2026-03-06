# Research: Jules Temporal External Events

## Decision 1: Reuse the existing Jules runtime gate everywhere

- **Decision**: Keep `moonmind/jules/runtime.py` and `settings.jules_runtime_gate` as the single Jules enablement contract for Temporal activities, API request validation, MCP tool discovery, dashboard runtime config, and worker runtime preflight.
- **Rationale**: The spec and source doc explicitly forbid a second Temporal-only enablement flag. Reusing the existing gate prevents drift in error messaging and availability behavior.
- **Alternatives considered**:
  - Add a Temporal-specific `JULES_TEMPORAL_ENABLED` flag: rejected because it would violate `DOC-REQ-003`.
  - Let each surface implement its own Jules availability check: rejected because it would create semantic drift and inconsistent operator behavior.

## Decision 2: Keep one shared Jules status normalizer for legacy and Temporal paths

- **Decision**: Continue to centralize Jules status mapping in `moonmind/jules/status.py` and make both Temporal activities and legacy software-polling worker code depend on it.
- **Rationale**: The doc requires raw status preservation plus one shared bounded normalization path. The repo already has this module and the worker runtime already consumes it.
- **Alternatives considered**:
  - Keep separate Temporal and legacy mappings: rejected because the spec explicitly forbids drift.
  - Push normalization into dashboard or API compatibility layers: rejected because status ownership belongs in the Jules integration implementation.

## Decision 3: Extend the current Temporal activity slice instead of replacing it

- **Decision**: Treat `integration.jules.start`, `integration.jules.status`, and `integration.jules.fetch_result` in `moonmind/workflows/temporal/activity_runtime.py` as the canonical runtime seam and finish their contract coverage there.
- **Rationale**: The repo already has registered activity names, worker-fleet wiring, and tests. Extending this slice is lower risk than creating a parallel abstraction.
- **Alternatives considered**:
  - Build a new provider-agnostic integrations service first: rejected because it delays delivery and is unnecessary for the current Jules-specific contract.
  - Route Jules through generic sandbox or workflow code: rejected because provider I/O belongs in integration activities.

## Decision 4: Correlation hints may be embedded in provider metadata, but MoonMind owns the durable record

- **Decision**: Keep `correlation_id` and idempotency information as MoonMind-owned state, while optionally embedding non-secret correlation hints in the Jules `metadata` field on create.
- **Rationale**: The source doc explicitly allows provider metadata for hints but forbids relying on it as the durable source of truth.
- **Alternatives considered**:
  - Depend on provider metadata as the only correlation store: rejected because provider-side data is not durable enough for retries and `Continue-As-New`.
  - Avoid metadata entirely: rejected because it gives up a useful hook for future callback correlation and operator debugging.

## Decision 5: `integration.jules.fetch_result` stays conservative

- **Decision**: Define `fetch_result` as artifact-backed terminal snapshot materialization plus MoonMind-authored summary/failure artifacts when available; do not assume logs, diffs, or downloadable outputs from Jules.
- **Rationale**: The current Jules adapter only exposes create/get/finish operations. The safest contract is to materialize the known task snapshot rather than inventing richer provider outputs.
- **Alternatives considered**:
  - Model `fetch_result` as a rich output export activity now: rejected because the provider contract does not support it.
  - Omit `fetch_result` until richer endpoints exist: rejected because the activity is already registered and the spec requires conservative result materialization now.

## Decision 6: Provider cancellation remains unsupported until Jules exposes a real cancel API

- **Decision**: Keep `integration.jules.cancel` as a reserved, not-yet-implemented contract name and report provider-side cancellation as unsupported today.
- **Rationale**: Truthful cancellation reporting is a direct requirement. The current adapter has no cancel endpoint, so fake success would be incorrect.
- **Alternatives considered**:
  - Mark workflow cancellation as equivalent to provider cancellation: rejected because it is false.
  - Quietly ignore provider cancellation semantics: rejected because operators need an honest final summary.

## Decision 7: Callback behavior is architecture-ready but not runtime-ready

- **Decision**: Keep the future callback shape tied to the generic `ExternalEvent` contract, but default `callback_supported=false` and do not plan a callback ingress implementation unless verified provider support exists.
- **Rationale**: The spec requires polling capability now and callback-first only when verified. This preserves the long-term architecture without overclaiming present behavior.
- **Alternatives considered**:
  - Claim callback support now and fill in later: rejected because it would violate the documented hybrid-repo reality.
  - Ignore callbacks completely in planning: rejected because the contract must remain callback-ready.

## Decision 8: Compatibility surfaces must preserve workflow identity separately from provider identity

- **Decision**: Keep MoonMind workflow/task identity primary in API and dashboard compatibility rows, and expose Jules `taskId` only as the external provider handle.
- **Rationale**: The migration rules explicitly prohibit treating Jules `taskId` as the durable MoonMind execution identifier.
- **Alternatives considered**:
  - Reuse Jules `taskId` as the compatibility `taskId`: rejected because it would break identity semantics and UI/API assumptions.
  - Hide provider identity entirely: rejected because operators still need the external handle and deep link.

## Decision 9: Runtime mode is a hard planning and implementation gate

- **Decision**: Treat this feature as runtime-mode delivery end to end: production code changes plus automated tests are mandatory, and docs-only completion is invalid.
- **Rationale**: The task objective and `FR-001`/`FR-017` make runtime scope explicit.
- **Alternatives considered**:
  - Ship only the docs contract and defer runtime code: rejected by the feature objective.
  - Treat tests as optional because code already exists: rejected because the feature specifically requires validation coverage.

## Decision 10: Validation should extend existing suites, not invent a new bespoke test harness

- **Decision**: Extend the existing Jules adapter, Temporal activity runtime, API router, dashboard runtime-config, and worker runtime suites, then validate with `./tools/test_unit.sh`.
- **Rationale**: The current repo already has targeted Jules tests in each relevant surface. Reusing them keeps coverage close to the implementation seams and follows repository policy.
- **Alternatives considered**:
  - Add a standalone one-off integration harness: rejected because the plan phase should prioritize the repository-standard validation path.
  - Rely only on manual testing: rejected because the spec requires automated validation tests.

## Result

- All planning unknowns are resolved without adding new flags, queues, or provider assumptions.
- Remaining future-facing work is intentionally limited to callback ingress and provider cancellation, both of which stay unimplemented until provider support is real.
