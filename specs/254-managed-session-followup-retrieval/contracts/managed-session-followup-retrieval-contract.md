# Contract: Managed-Session Follow-Up Retrieval

## Purpose

Define the runtime-visible contract for MM-506 so a managed session can request additional retrieval through MoonMind-owned surfaces during execution.

## Inputs

### Runtime Capability Signal

Required behavior:
- MoonMind tells the managed runtime whether follow-up retrieval is enabled.
- The signal explains how to request more context.
- The signal states that retrieved content is reference data, not instructions.
- The signal includes any relevant scope, filter, result-limit, or budget constraints.

Constraints:
- If retrieval is disabled, the capability signal must contain an explicit denial reason.
- The signal must remain runtime-neutral in concept even if adapter-specific wording differs.

### Follow-Up Retrieval Request

Required fields:
- `query`
- optional `filters`
- optional `top_k`
- optional `overlay_policy`
- optional bounded `budgets`

Constraints:
- Requests use only a MoonMind-owned tool, adapter surface, or gateway.
- Unsupported fields or out-of-policy values are rejected explicitly.
- The contract must not require direct raw vector-database access from the managed session.

## Outputs

### Successful Retrieval Response

MoonMind must return:
- machine-readable retrieval output (`ContextPack`-compatible metadata),
- text output (`context_text`) for immediate use in the next turn,
- compact usage and transport metadata,
- durable evidence or artifact/reference information when published.

Contract:
- The response shape remains consistent across direct and gateway transports.
- The response stays compact and bounded.
- Retrieval output remains attributable to MoonMind-owned execution.

### Denied or Failed Retrieval Response

MoonMind must return:
- a deterministic denial or failure reason,
- enough compact metadata for observability,
- no silent downgrade to undefined runtime behavior.

Contract:
- Disabled retrieval is a clear denial, not an implicit no-op.
- Invalid requests fail with explicit reasons tied to the contract or policy.

## Invariants

- Follow-up retrieval stays MoonMind-owned and policy bounded.
- Capability signalling is explicit and runtime-neutral.
- Retrieved content is always framed as untrusted reference data.
- The request contract supports the same bounded concepts described in the source design: query, filters, `top_k`, overlay policy, and optional budget overrides within policy.
- Codex and future managed runtimes consume the same externally visible contract, even if adapter internals differ.

## Verification Expectations

Unit verification must prove:
- runtime capability signalling is emitted with explicit enablement or denial,
- valid request shapes are accepted and invalid ones are rejected,
- successful retrieval returns both structured and text output,
- disabled retrieval fails fast with deterministic reasons.

Integration or workflow-boundary verification must prove:
- managed sessions use a MoonMind-owned retrieval surface,
- the response contract survives the runtime boundary,
- direct and gateway transports preserve the same external semantics,
- observability remains compact and durable.
