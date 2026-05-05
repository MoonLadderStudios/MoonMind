# Contract: Retrieval Transport and Configuration Separation

## Purpose

Define the MoonMind-owned contract for keeping Workflow RAG configuration separate from managed-runtime provider profiles while supporting direct, gateway, and explicit degraded fallback retrieval modes under MM-508.

## Inputs

### Retrieval Configuration Surface

Required behavior:
- Retrieval settings resolve from MoonMind-owned configuration and environment inputs.
- Retrieval configuration includes embedding provider/model, vector-store connection, retrieval gateway URL, overlay mode, and retrieval budgets.
- Retrieval configuration remains distinct from managed-runtime provider-profile launch fields.

### Managed Runtime Provider Profile Surface

Required behavior:
- Provider profiles remain focused on runtime launch, provider identity, runtime materialization, and runtime credential handling.
- Provider profiles do not become the default generic source of embedding credentials or retrieval transport policy.

## Outputs

### Transport Resolution

Contract:
- MoonMind resolves a transport of `gateway`, `direct`, or explicit degraded `local_fallback`.
- Gateway is preferred when MoonMind owns outbound retrieval or runtime embedding credentials are unavailable by default.
- Gateway requests from managed runtimes use scoped RetrievalGateway-token auth (`MOONMIND_RETRIEVAL_TOKEN`) rather than legacy worker-token auth.
- Direct remains available when environment and policy permit embedding and Qdrant access.
- Local fallback remains explicit and degraded.

### Retrieval Metadata

Contract:
- Compact retrieval metadata records the selected transport and retrieval artifact/ref.
- Degraded fallback remains observable in metadata and runtime-facing instruction context.
- Retrieval metadata stays separate from provider-profile launch metadata.

## Invariants

- Workflow RAG configuration is a shared MoonMind contract, not a provider-profile-specific behavior.
- Runtime provider profiles shape launch behavior but do not implicitly own retrieval transport choice.
- Overlay, filters, top-k, and budget knobs remain shared retrieval settings across supported transports.
- Local fallback is explicit degraded retrieval, not silent semantic retrieval.
- Retrieval transport decisions must not expand managed-runtime authority into unrestricted control-plane or raw datastore access.

## Verification Expectations

Unit verification must prove:
- retrieval settings resolve independently from provider-profile launch fields,
- gateway preference and direct availability behave deterministically from retrieval configuration,
- local fallback remains gated and transport-visible,
- compact metadata records selected transport separately from provider-profile metadata.

Integration or workflow-boundary verification must prove:
- managed-runtime boundaries preserve the same retrieval-versus-profile separation,
- overlay and budget knobs remain coherent across supported transport paths,
- gateway preference and degraded fallback remain externally visible and policy-bounded.
