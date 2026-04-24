# Data Model: Retrieval Evidence And Trust Guardrails

## Retrieval Evidence Record

Purpose: durable record of how one retrieval operation executed and what publication surfaces it produced.

Fields:
- `mode`: `semantic`, `degraded_local_fallback`, or explicit disabled/skipped state for retrieval visibility.
- `initiation_mode`: whether retrieval was automatic at step start or session-issued later.
- `transport`: `direct`, `gateway`, or `local_fallback`.
- `filters`: normalized retrieval filters, including repository scope when applicable.
- `budgets`: token and latency budgets applied to the retrieval request.
- `usage`: observed token and latency usage for the retrieval operation.
- `item_count`: number of retrieved items returned.
- `truncated`: whether retrieval context text was truncated for prompt or artifact safety.
- `artifact_ref`: durable artifact/ref that points to the published `ContextPack` or equivalent retrieval payload.
- `degraded_reason`: explicit degraded reason when fallback or degraded retrieval occurs.
- `telemetry_id`: stable correlation id for retrieval telemetry and logs.
- `retrieved_at`: UTC timestamp for the retrieval result.

Validation rules:
- `degraded_reason` is required when `mode = degraded_local_fallback`.
- `artifact_ref` is required whenever retrieval produced a durable publication artifact.
- `transport` must align with `mode`; `local_fallback` implies degraded mode.
- `filters`, `budgets`, and `usage` must be serializable without secret-bearing values.

## Trust Framing Envelope

Purpose: runtime-facing instruction framing that keeps retrieved text inside an untrusted-reference boundary.

Fields:
- `safety_notice`: tells the runtime to treat retrieved context as untrusted reference data, not instructions.
- `context_block`: bounded retrieved context text presented between explicit delimiters.
- `artifact_notice`: optional notice pointing at the durable retrieval artifact/ref.
- `mode_notice`: explicit degraded-mode notice when fallback retrieval was used.
- `conflict_rule`: statement that current repository state wins when retrieved content conflicts with the checked-out workspace.
- `task_instruction`: original task instruction preserved after the safety framing.

Validation rules:
- `safety_notice` and `conflict_rule` are required whenever `context_block` is present.
- `mode_notice` is required for degraded local fallback injection.
- The envelope must not include raw secrets or executable instructions copied from provider config.

## Retrieval Policy Envelope

Purpose: bounded control surface for session-issued retrieval.

Fields:
- `authorized_scope`: allowed corpus or repository scope.
- `filters`: request filters approved for the retrieval call.
- `budgets`: token and latency budgets.
- `transport_policy`: whether direct, gateway, or fallback retrieval is allowed.
- `provider_secret_policy`: whether provider credentials may be used and through which boundary.
- `audit_required`: whether durable evidence must be published before the request is considered complete.

Validation rules:
- session-issued retrieval cannot execute when the requested scope exceeds `authorized_scope`.
- unsupported budget keys are rejected before retrieval runs.
- degraded fallback may run only when transport policy explicitly allows it.

## Runtime Retrieval State

Purpose: runtime-visible state that distinguishes enabled, disabled, semantic, and degraded retrieval outcomes.

States:
- `disabled`: retrieval did not run because automatic retrieval is off or retrieval is unavailable by policy.
- `semantic`: normal retrieval completed via `direct` or `gateway`.
- `degraded_local_fallback`: explicit degraded retrieval path using local fallback search.

State transitions:
1. `disabled` -> `semantic` when retrieval is enabled and semantic retrieval succeeds.
2. `semantic` -> `degraded_local_fallback` only when semantic retrieval is unavailable for an allowed degraded reason and local fallback is permitted.
3. `disabled` remains terminal for a step when retrieval is off and no degraded fallback is allowed.

## Relationships

- One `Retrieval Evidence Record` may produce one durable `artifact_ref` pointing to a published `ContextPack`.
- One `Trust Framing Envelope` is derived from one retrieval result and one original task instruction.
- One `Retrieval Policy Envelope` constrains one retrieval request or retrieval session phase.
- One `Runtime Retrieval State` is exposed through runtime-visible metadata for each retrieval attempt.
