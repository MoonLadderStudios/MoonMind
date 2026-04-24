# Data Model: Retrieval Transport and Configuration Separation

## Purpose

Define the runtime data surfaces relevant to MM-508 so retrieval configuration stays separate from managed-runtime provider profiles while transport selection remains observable and bounded.

## Entities

### Retrieval Configuration

Fields:
- `embedding_provider`: selected embedding provider for retrieval
- `embedding_model`: selected embedding model for retrieval
- `embedding_dimensions`: optional embedding dimension override
- `vector_collection`: canonical retrieval collection name
- `qdrant_url` / `qdrant_host` / `qdrant_port`: vector-store connection details
- `retrieval_gateway_url`: optional MoonMind-owned retrieval gateway URL
- `similarity_top_k`: retrieval result count limit
- `max_context_chars`: maximum injected context size
- `overlay_mode`: overlay retrieval behavior
- `rag_enabled` / `qdrant_enabled`: execution guards

Validation rules:
- retrieval settings resolve from retrieval config sources, not from provider-profile launch fields
- transport selection may observe retrieval-gateway presence and provider-specific credential availability
- unsupported embedding providers fail fast as retrieval configuration errors

### Managed Runtime Provider Profile

Fields already present in the existing profile system:
- `profile_id`
- `runtime_id`
- `provider_id`
- `credential_source`
- `runtime_materialization_mode`
- `secret_refs`
- `env_template`
- `file_templates`
- runtime launch metadata such as model defaults and lease policies

Validation rules:
- runtime profile fields remain focused on runtime launch and provider shaping
- provider profiles may supply runtime credentials, but they are not the generic retrieval-credential authority by default
- retrieval transport choice must not be encoded as profile-only semantics

### Retrieval Transport Resolution

Fields:
- `preferred_transport`: requested or inferred transport, when present
- `resolved_transport`: one of `direct`, `gateway`, or explicit degraded `local_fallback`
- `resolution_reason`: normalized reason for executable or degraded behavior

Validation rules:
- `gateway` is preferred when a retrieval gateway is configured and direct embedding credentials should not be required in the runtime environment
- `direct` remains valid when policy and environment permit embedding plus Qdrant access
- `local_fallback` is only allowed for explicit degraded reasons

### Retrieval Metadata Contract

Fields:
- `retrievedContextArtifactPath`
- `latestContextPackRef`
- `retrievedContextTransport`
- `retrievedContextItemCount`
- `retrievalDurabilityAuthority`
- `sessionContinuityCacheStatus`

Validation rules:
- metadata stays compact and transport-observable
- degraded fallback is visible through transport and reason metadata
- metadata remains separate from provider-profile launch metadata

## Relationships

- Managed runtime executions may reference a provider profile for launch and a retrieval configuration for Workflow RAG in the same request, but those concerns remain distinct.
- Retrieval transport resolution consumes retrieval configuration plus environment capability signals and writes compact retrieval metadata.
- Provider profiles influence runtime launch but should not become the default retrieval-transport or embedding-credential authority.

## State Transitions

1. Retrieval settings resolve from runtime environment and shared config.
2. Managed runtime launch resolves profile metadata independently.
3. Retrieval transport resolves to `gateway` or `direct`, or degrades to explicit `local_fallback` only when allowed.
4. Retrieval executes and records compact metadata with selected transport.
5. Downstream runtime boundaries consume retrieval metadata without inferring transport ownership from provider-profile semantics.
