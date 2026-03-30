# Temporal Error Taxonomy

Status: **Core policy document** (partially enforced in current activity definitions; broader adoption ongoing)
Last updated: 2026-03-30
Related:
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](./ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](./ActivityCatalogAndWorkerTopology.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)

---

## 1. Purpose

This document defines how MoonMind classifies Temporal `ApplicationError` types and related failures as:

- **retryable**
- **non-retryable**
- **retryable but usually handled through a higher-level cooldown or orchestration loop**

The goal is to keep error handling consistent across:

- planning and review activities
- provider integration activities
- managed runtime activities
- provider-profile support activities
- `MoonMind.AgentRun` orchestration logic

This taxonomy is the policy source of truth for how MoonMind should think about failures. Individual activity families may still be adopting the full taxonomy in code, but new work should align with this document.

---

## 2. Core principles

### 2.1 Retry only when another attempt could realistically succeed

A failure is **retryable** only when the same request can plausibly succeed later without changing the business input.

Examples:

- transient network failures
- provider outages
- brief infrastructure unavailability
- optimistic race conditions
- short-lived transport timeouts

### 2.2 Do not retry invalid business input

A failure is **non-retryable** when the request is malformed, unsupported, or structurally invalid.

Examples:

- malformed payloads
- missing required artifacts
- unknown contract states
- unsupported provider status mappings
- invalid configuration that no number of retries will repair

### 2.3 Normalize at the activity boundary

For true agent-runtime execution, canonical contract violations must be detected at the adapter or activity boundary.

That means:

- provider status normalization belongs in adapters or integration activities
- managed runtime status normalization belongs in the managed runtime layer
- workflow code should not “repair” malformed provider payloads on the fly

If an activity cannot return a valid canonical `AgentRunHandle`, `AgentRunStatus`, or `AgentRunResult`, that is a contract failure and should generally be treated as **non-retryable**.

### 2.4 Some failures are retryable in theory but are better handled by orchestration policy

A provider `429` is a good example.

It may technically be retryable, but MoonMind often handles it through:

- cooldown signaling
- slot release and reacquisition
- manager-driven throttling
- continue-as-new or delayed retry loops

Those failures should not be treated like ordinary tight-loop transient retries.

---

## 3. Error classes

MoonMind uses three practical categories.

## 3.1 Retryable

Safe to retry automatically with bounded backoff.

## 3.2 Non-retryable

Should fail immediately because the request, contract, or configuration is invalid.

## 3.3 Retryable-with-policy

Could succeed later, but should usually be handled through a higher-level policy rather than naive immediate retries.

---

## 4. Canonical classifications

## 4.1 Retryable errors

The following are generally retryable:

- transient network failures
- DNS resolution failures
- connection resets
- short-lived upstream timeouts
- temporary provider unavailability
- temporary storage service unavailability
- optimistic concurrency races
- activity worker restarts mid-call where the underlying side effect is idempotent

These should usually use bounded exponential backoff.

## 4.2 Retryable-with-policy errors

The following are usually handled through special policy rather than plain repeated retries:

- provider rate limits
- managed runtime capacity exhaustion
- temporary auth-profile slot contention
- provider-side “try again later” states when the provider gives explicit backoff guidance

Examples:

- `429`
- `RESOURCE_EXHAUSTED`
- profile cooldown windows
- queue-capacity exhaustion that is expected to clear after delay

These failures are often coordinated by:

- `ProviderProfileManager`
- cooldown reporting
- delayed re-request of slots
- continue-as-new retry loops in `MoonMind.AgentRun`

## 4.3 Non-retryable errors

The following are generally non-retryable:

- malformed activity input payloads
- invalid or missing required artifact refs
- invalid configuration for the requested runtime/provider
- unsupported or unknown provider lifecycle status values
- canonical contract-shape violations
- missing required auth profiles
- impossible execution mode combinations
- unsupported runtime/provider IDs
- deterministic validation failures that require a new request

---

## 5. Standard non-retryable error codes

These codes should be treated as non-retryable unless a specific activity family explicitly documents a different rule.

## 5.1 `INVALID_INPUT`

Use when:

- the request payload is malformed
- required parameters are missing
- the wrong artifact type or schema was supplied
- fields are structurally invalid
- the request violates a documented contract

Examples:

- malformed `AgentExecutionRequest`
- invalid plan structure supplied to validation
- missing required `run_id`
- invalid enum-like parameters that are not supported

Classification: **non-retryable**

## 5.2 `ProfileResolutionError`

Use when:

- no enabled provider profiles exist for the requested runtime
- the requested provider selector cannot resolve to any usable profile
- managed execution cannot continue because the runtime/profile configuration is not satisfiable

Examples:

- no enabled `gemini_cli` profiles
- selector requests provider `minimax` but none is configured for the chosen runtime
- profile resolution logic determines the request cannot ever succeed as submitted

Classification: **non-retryable**

## 5.3 `UnsupportedStatus`

Use when:

- a provider emits a lifecycle status MoonMind does not know how to map
- an external or managed runtime layer returns a state outside the canonical `AgentRunState` set
- an integration activity cannot produce a valid canonical status contract

Important rule:

- this should be raised at the adapter or activity boundary
- this should **not** be deferred to workflow-side coercion logic

Classification: **non-retryable**

## 5.4 `SlotAcquisitionTimeout`

Use when:

- managed execution waits too long for provider-profile slot assignment
- the manager appears stuck or unavailable after bounded recovery attempts
- the orchestration path has exhausted its manager-reset or reacquisition strategy

Examples:

- `MoonMind.AgentRun` times out after repeated manager reset attempts
- slot assignment never occurs despite a valid request and bounded retries

Classification: **non-retryable** for the current attempt

Notes:

- this is not the same thing as ordinary short-lived slot contention
- short-lived contention should remain inside the managed wait path
- this code is for bounded orchestration failure after recovery logic is exhausted

---

## 6. Canonical contract failures

For true agent-runtime activities, MoonMind’s canonical contract rule is:

- `integration.<provider>.start` must return `AgentRunHandle`
- `integration.<provider>.status` must return `AgentRunStatus`
- `integration.<provider>.fetch_result` must return `AgentRunResult`
- `integration.<provider>.cancel` must return `AgentRunStatus`
- `agent_runtime.status` must return `AgentRunStatus`
- `agent_runtime.fetch_result` must return `AgentRunResult`

Failures to satisfy these contracts should generally be treated as **non-retryable contract failures** unless the failure is clearly caused by a transient transport issue before a real payload was received.

Examples of non-retryable contract failures:

- missing required canonical fields
- provider returns an unrecognized top-level shape
- invalid status that cannot be normalized
- malformed result payload that cannot be represented as `AgentRunResult`

Examples of retryable transport failures:

- HTTP timeout before any valid provider payload was received
- connection reset while polling status
- temporary 502/503 from provider API

---

## 7. Family-specific guidance

## 7.1 Planning and review activities

Examples:

- `plan.validate`
- `step.review`

Typical retryable failures:

- transient LLM provider failures
- transport failures
- temporary upstream timeouts

Typical non-retryable failures:

- `INVALID_INPUT`
- malformed plan structure
- invalid registry snapshot reference
- invalid review payload schema

## 7.2 Integration activities

Examples:

- `integration.jules.start`
- `integration.jules.status`
- `integration.codex_cloud.fetch_result`

Typical retryable failures:

- network failures
- provider 5xx responses
- temporary provider outages

Typical retryable-with-policy failures:

- `429`
- provider-directed backoff conditions

Typical non-retryable failures:

- `UnsupportedStatus`
- malformed provider payload that cannot be normalized
- invalid request schema
- unsupported provider configuration

## 7.3 Managed runtime activities

Examples:

- `agent_runtime.launch`
- `agent_runtime.status`
- `agent_runtime.fetch_result`
- `agent_runtime.cancel`

Typical retryable failures:

- short-lived supervisor/store access failures
- temporary artifact publication failures
- transient filesystem or process-launch races that are explicitly made idempotent

Typical retryable-with-policy failures:

- provider rate-limit conditions surfaced through managed runtime results
- profile cooldown conditions

Typical non-retryable failures:

- `ProfileResolutionError`
- `SlotAcquisitionTimeout`
- invalid managed runtime configuration
- malformed managed runtime status/result payloads

## 7.4 Provider-profile support activities

Examples:

- `provider_profile.list`
- `provider_profile.ensure_manager`
- `provider_profile.verify_lease_holders`

Typical retryable failures:

- transient DB failures
- Temporal client communication failures
- temporary manager startup races

Typical non-retryable failures:

- invalid runtime ID
- malformed profile rows that violate expected schema
- impossible selector/config combinations

---

## 8. Rate limits and cooldowns

Rate-limit failures deserve special handling.

## 8.1 Why `429` is special

A `429` often means:

- the request is valid
- the provider is healthy
- retrying immediately is wasteful
- a delayed retry may succeed

So MoonMind treats `429` as **retryable-with-policy**, not just “retry immediately.”

## 8.2 Expected handling patterns

Examples of acceptable handling:

- report cooldown to `ProviderProfileManager`
- release the current slot
- wait for cooldown expiry
- reacquire a slot
- continue-as-new when history growth or retry count warrants it

The right response is usually orchestration-aware backoff, not aggressive activity-level repetition.

---

## 9. Retry policy guidance

## 9.1 Use bounded retries

Even retryable failures should use:

- exponential backoff
- maximum interval caps
- maximum attempt caps

Do not allow unbounded activity-level retry loops.

## 9.2 Prefer typed classification to string matching

When possible, raise explicit `ApplicationError` types or use stable internal error codes rather than fragile message parsing.

## 9.3 Keep destructive work idempotent before marking it retryable

A side-effecting operation should only be classified as retryable if one of the following is true:

- it is naturally idempotent
- it uses an idempotency key
- duplicate execution is explicitly safe

---

## 10. Workflow vs activity responsibilities

## 10.1 Activities own failure translation

Activities should classify low-level exceptions into MoonMind-meaningful categories where possible.

Examples:

- provider transport failure → retryable integration failure
- unsupported provider state → `UnsupportedStatus`
- malformed request schema → `INVALID_INPUT`

## 10.2 Workflows own orchestration response

Workflows decide what to do with activity failures:

- fail fast
- continue
- trigger cooldown logic
- reschedule work
- continue-as-new
- mark task status and update visibility

## 10.3 Workflows should not repair malformed contracts

Workflow code should not become a compatibility layer for malformed provider/runtime payloads. That logic belongs in adapters and activities.

---

## 11. Current implementation notes

The current runtime already uses parts of this taxonomy in code.

Examples include:

- `INVALID_INPUT`
- `ProfileResolutionError`
- `UnsupportedStatus`
- `SlotAcquisitionTimeout`

Not every activity family has fully adopted the full taxonomy yet. This document defines the direction new code should follow.

---

## 12. Temporal Python SDK limitation note

### 12.1 Signal-with-start from workflow context

The current `temporalio` Python SDK does not provide a `signal_with_start` method on `workflow.ExternalWorkflowHandle` objects for use inside workflow code.

That is why `MoonMind.AgentRun` uses a fallback pattern like:

- try to signal the external workflow
- if it does not exist, invoke a support activity such as `provider_profile.ensure_manager`
- reacquire the handle
- retry the signal

This is an implementation constraint, not an error-classification rule, but it matters when reasoning about certain manager-startup failures.

---

## 13. Recommended starter mapping for activity catalogs

When a catalog or family wants a default non-retryable set, the usual starter list is:

- `INVALID_INPUT`
- `ProfileResolutionError`
- `UnsupportedStatus`

Families that perform bounded orchestration recovery may also add:

- `SlotAcquisitionTimeout`

Additional family-specific codes may be introduced when they have clear semantic value and stable meaning.

---

## 14. Summary

MoonMind’s error taxonomy is built around a simple rule:

- retry when the same request could reasonably succeed later
- fail immediately when the request, contract, or configuration is invalid
- handle rate limits and slot contention through orchestration-aware policy rather than naive immediate retries

The most important non-retryable classes today are:

- `INVALID_INPUT`
- `ProfileResolutionError`
- `UnsupportedStatus`
- `SlotAcquisitionTimeout`

The most important special-case retryable-with-policy class is:

- provider rate limits such as `429`

For true agent-runtime execution, canonical contract failures must be caught at the adapter or activity boundary, not silently papered over in workflow code.