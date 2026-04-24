# Contract: Managed-Session Initial Retrieval Context

## Purpose

Define the runtime-visible contract for MoonMind-owned initial retrieval context assembly and publication for managed-session startup under MM-505.

## Inputs

### Managed-Session Execution Request

Required behavior:
- Provides the instruction text or instruction reference used as the retrieval query source.
- Provides run/job identity sufficient for retrieval telemetry and overlay behavior.
- May provide repository metadata used to derive retrieval filters.

Constraints:
- Retrieval preparation happens before the runtime begins normal task work.
- Missing or empty instructions result in no injected retrieval context.

### Retrieval Runtime Settings

Required behavior:
- Resolve whether retrieval is executable.
- Resolve transport, overlay policy, and optional budgets.
- Preserve MoonMind-owned retrieval policy regardless of whether transport is direct or gateway.

## Outputs

### Durable Retrieval Output

MoonMind must publish the initial retrieval result as durable startup evidence.

Contract:
- The retrieval result is represented as a ContextPack-like durable artifact or ref.
- Large retrieved bodies do not become unbounded durable workflow payloads.
- The durable output remains available for later verification and session recovery workflows.

### Runtime Instruction Surface

MoonMind must present the managed runtime with an instruction surface that includes:
- the retrieved context block,
- explicit framing that retrieved text is untrusted reference data,
- the original instruction body.

Contract:
- If retrieval produces no items, MoonMind may preserve the original instruction unchanged.
- If retrieval produces items, the runtime instruction must include both the framing and the original instruction.

## Invariants

- Initial retrieval uses embedding-backed search and deterministic ContextPack assembly without a separate generative retrieval hop.
- Durable publication is the authoritative startup truth for retrieved context.
- The contract stays compatible with current Codex-style workspace preparation and any future managed runtime that consumes MoonMind-owned retrieval context.
- Codex and Claude startup paths both consume the same shared `ContextInjectionService` contract during workspace preparation.
- Retrieval transport choice does not change the visible contract at the runtime boundary.

## Verification Expectations

Unit verification must prove:
- retrieval preparation runs before runtime command launch,
- ContextPack publication occurs when retrieval succeeds,
- instruction framing treats retrieved text as untrusted reference data.

Integration or workflow-boundary verification must prove:
- durable retrieval publication remains compact at runtime boundaries,
- reusable runtime behavior does not rely on a Codex-only bespoke contract,
- transport and policy choices do not alter the contract’s external semantics.
