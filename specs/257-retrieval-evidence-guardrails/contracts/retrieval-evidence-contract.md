# Contract: Retrieval Evidence And Trust Guardrails

## Purpose

Define the runtime-facing and durable-evidence contract for MM-509 so direct, gateway, and degraded local-fallback retrieval paths publish comparable evidence and preserve the same trust boundary.

## Durable Retrieval Evidence

Required fields for every retrieval operation:
- `retrievalMode`
- `retrievedContextTransport`
- `retrievedContextItemCount`
- `retrievedContextArtifactPath` or equivalent durable `artifact_ref`
- `latestContextPackRef` when a `ContextPack` artifact is published
- `retrievalDurabilityAuthority`
- `sessionContinuityCacheStatus`
- `filters`
- `budgets`
- `usage`
- `telemetry_id`
- `retrieved_at`
- `retrievalDegradedReason` when retrieval is degraded
- explicit initiation mode metadata for automatic versus session-issued retrieval
- explicit truncation metadata when retrieved context text was shortened

Contract rules:
- `retrievalMode = semantic` for direct or gateway semantic retrieval.
- `retrievalMode = degraded_local_fallback` for local fallback retrieval.
- `retrievalDegradedReason` is forbidden for normal semantic retrieval and required for degraded retrieval.
- durable evidence must point to artifact/ref-backed retrieval publication instead of embedding large retrieved bodies in workflow payloads.

## Trust Framing Contract

When retrieved context is injected into runtime instructions, the envelope must include:
- a safety notice stating retrieved context is untrusted reference data, not instructions
- explicit delimiters for the retrieved context block
- a conflict rule that prefers current repository state over stale retrieved content
- the original task instruction after the safety framing
- an explicit degraded-mode notice when local fallback retrieval was used
- an artifact notice when a durable retrieval artifact/ref exists

## Secret-Handling Contract

Durable retrieval artifacts, workflow payloads, and runtime metadata must exclude:
- raw provider API keys
- OAuth tokens
- bearer tokens used for retrieval gateway access
- generated secret-bearing config bodies
- serialized secret resolution results

Allowed durable values are limited to sanitized metadata such as transport, counts, budgets, usage, artifact refs, telemetry ids, and normalized degraded reasons.

## Policy Envelope Contract

Session-issued retrieval must be rejected or degraded safely when any of the following fail:
- authorized corpus or repository scope
- supported filters
- token or latency budgets
- transport policy
- provider/secret policy
- audit/evidence publication requirement

The gateway and direct retrieval paths must enforce the same logical policy envelope even if the implementation boundary differs.

## Cross-Runtime Consistency Contract

Codex and any additional managed runtime that adopts Workflow RAG must:
- publish the same core durable retrieval evidence fields
- apply the same trust framing semantics
- preserve explicit degraded-mode visibility
- keep retrieval truth artifact/ref-backed rather than runtime-local only

Runtime-specific wrappers may change delivery mechanics, but they may not weaken the evidence or trust contract.
