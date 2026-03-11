# Research: Activity Catalog and Worker Topology

## Decision 1: Extend the existing Temporal catalog/runtime modules instead of creating a parallel worker abstraction

- **Decision**: Treat `moonmind/workflows/temporal/activity_catalog.py`, `activity_runtime.py`, and `artifacts.py` as the implementation baseline and expand them to cover the full canonical system.
- **Rationale**: The repo already contains stable queue names, activity definitions, artifact wrappers, and runtime helpers. Replacing them with a second topology layer would create duplicate contract ownership.
- **Alternatives considered**:
  - Build a separate worker registry module from scratch: rejected because it duplicates existing catalog logic and increases drift risk.
  - Push routing logic into compose/environment only: rejected because routing contracts must stay testable in Python runtime code.

## Decision 2: Keep the v1 queue topology fixed to one workflow queue plus four activity queues

- **Decision**: Use `mm.workflow`, `mm.activity.artifacts`, `mm.activity.llm`, `mm.activity.sandbox`, and `mm.activity.integrations` as the only v1 queues.
- **Rationale**: This matches the source document exactly, keeps the topology small, and preserves queue semantics as internal routing only.
- **Alternatives considered**:
  - Add provider-specific LLM queues now: rejected because v1 explicitly defers them.
  - Reuse legacy queue names or orchestration queues: rejected because this feature defines a Temporal-native contract.

## Decision 3: Resolve routing per invocation from activity catalog plus skill capability metadata

- **Decision**: Keep routing selection capability-based per activity invocation, with `mm.tool.execute` resolved by the pinned skill registry snapshot and explicit catalog entries used for curated activity types.
- **Rationale**: Routing by capability preserves least-privilege fleet assignment and matches the documented hybrid binding model.
- **Alternatives considered**:
  - Route by workflow type: rejected because the source doc forbids workflow-type-based routing.
  - Let worker code infer a queue from inputs at runtime: rejected because it makes routing nondeterministic and hard to validate.

## Decision 4: Standardize business payloads around artifact references and idempotency keys

- **Decision**: Keep side-effecting activity payloads small and centered on `correlation_id`, `idempotency_key`, `input_refs`, and compact `parameters`, while runtime metadata is derived from Temporal context.
- **Rationale**: This keeps workflow history bounded, aligns with existing `ArtifactRef` and `ExecutionRef` contracts, and avoids duplicating execution identity fields into every business payload.
- **Alternatives considered**:
  - Inline large logs or request/result blobs: rejected because it bloats history and weakens retry safety.
  - Require workflow/run/activity IDs in every request body: rejected because the runtime already provides that context.

## Decision 5: Enforce explicit skill bindings as fail-closed exceptions

- **Decision**: Preserve `mm.tool.execute` as the default path and require registry-declared justification for any explicit activity binding.
- **Rationale**: The source document and feature clarification both treat explicit bindings as exceptions for stronger isolation, specialized credentials, or clearer routing only.
- **Alternatives considered**:
  - Allow any skill to bind directly to any explicit activity type: rejected because it bypasses least-privilege routing and destabilizes the catalog.
  - Remove explicit binding support entirely: rejected because the canonical model intentionally allows curated exceptions.

## Decision 6: Materialize each fleet as a dedicated compose service with least-privilege configuration

- **Decision**: Add separate workflow, artifacts, llm, sandbox, and integrations worker services in Docker Compose, each bootstrapped from the same Python worker entrypoint with fleet-specific queue registration and env.
- **Rationale**: This matches the worker-topology contract and makes privilege separation, scaling, and operational diagnostics concrete.
- **Alternatives considered**:
  - Run one multipurpose worker process for all activity queues: rejected because it collapses isolation boundaries and secret scope.
  - Keep the topology as documentation only: rejected because runtime implementation is required.

## Decision 7: Complete the sandbox family with idempotent workspace lifecycle semantics

- **Decision**: Implement `sandbox.checkout_repo`, `sandbox.apply_patch`, and `sandbox.run_tests` in addition to `sandbox.run_command`, with workspace refs keyed for retry safety.
- **Rationale**: The source document defines the full sandbox family, and the current runtime only covers command execution. Safe retries require durable workspace identity, bounded destructive retries, and heartbeats.
- **Alternatives considered**:
  - Keep only `sandbox.run_command`: rejected because it does not satisfy the canonical activity catalog.
  - Perform repo checkout or patch application directly in workflow code: rejected because filesystem side effects must stay in activities.

## Decision 8: Keep integration execution callback-first with bounded polling fallback

- **Decision**: Retain `integration.jules.start/status/fetch_result` behind the Jules adapter and design workflow coordination around callback-first completion with polling only as fallback.
- **Rationale**: This minimizes long-running activity duration while preserving recoverability when callbacks are unavailable.
- **Alternatives considered**:
  - Model provider work as one long-running activity: rejected because it weakens heartbeat/cancellation behavior and complicates retries.
  - Move provider calls into sandbox workers: rejected because provider credentials must stay isolated from sandbox execution.

## Decision 9: Reuse existing artifact and redaction primitives for observability

- **Decision**: Persist large logs and diagnostics through the Temporal artifact system and reuse `SecretRedactor` plus structured logging/metrics patterns already present elsewhere in MoonMind.
- **Rationale**: The repo already has redaction logic and artifact-backed diagnostics patterns; reusing them reduces security drift and implementation cost.
- **Alternatives considered**:
  - Store all logs only in worker stdout: rejected because the source doc requires artifact-backed large logs and structured summaries.
  - Build a separate bespoke telemetry store: rejected because it adds another persistence surface without improving contract fidelity.

## Decision 10: Keep MinIO private in local/dev even when app auth is disabled

- **Decision**: Continue to treat `AUTH_PROVIDER=disabled` as an API auth-mode choice only; artifact blob storage remains internal-network-only and accessed with service credentials.
- **Rationale**: This preserves the source document's local security posture and matches the existing artifact storage design direction.
- **Alternatives considered**:
  - Expose MinIO publicly for convenience: rejected because it violates least-privilege and the documented local-dev posture.
  - Disable preview/redaction flows in local mode: rejected because security behavior should remain contract-consistent in local validation.

## Decision 11: Runtime implementation mode is the non-negotiable completion gate

- **Decision**: Keep this feature in runtime implementation mode, requiring production code and automated validation tests.
- **Rationale**: The task objective explicitly requires full implementation, not documentation-only output.
- **Alternatives considered**:
  - Stop at specs/contracts: rejected as non-compliant with FR-001.
  - Ship runtime code without repository-standard validation: rejected because test coverage is part of the feature contract.
