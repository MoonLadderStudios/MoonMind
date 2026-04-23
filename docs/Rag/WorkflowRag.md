# Workflow RAG – Managed Session Retrieval

**Status:** Draft
**Owners:** MoonMind Engineering
**Last Updated:** 2026-04-22

> **See also:**
> - [ManagedAgentArchitecture.md](../ManagedAgents/ManagedAgentArchitecture.md)
> - [SharedManagedAgentAbstractions.md](../ManagedAgents/SharedManagedAgentAbstractions.md)
> - [ProviderProfiles.md](../Security/ProviderProfiles.md)
> - [ManifestIngestDesign.md](./ManifestIngestDesign.md)
> - [LlamaIndexManifestSystem.md](./LlamaIndexManifestSystem.md)

## 1. Summary

This document defines how **document retrieval** works for **managed sessions** in MoonMind.

MoonMind’s managed-agent architecture is **managed-session first**. A managed session is the runtime-owned work session, but MoonMind remains the owner of durable context assembly, artifact publication, and orchestration. Workflow RAG is therefore **not** primarily a chat-style “agent remembers to search” feature. Its primary job is to let MoonMind assemble relevant document and code context, publish that context as durable artifacts and refs, and deliver it into managed sessions at the right step boundaries.

The retrieval path is intentionally lean:

**(embed query) → (vector search) → (build `ContextPack`) → (inject or publish retrieved context)**

The retrieval path does **not** require an additional general-purpose chat/completions LLM hop upstream. It may use an embedding model plus Qdrant, then hand the resulting `ContextPack` to the managed runtime.

A managed session may also perform additional retrieval later in the run when RAG is enabled. That follow-up retrieval should use the same MoonMind retrieval contract, budgets, filters, and artifact discipline rather than inventing a separate runtime-specific RAG model.

This document focuses on **managed-session usage**. Ad hoc or conversational retrieval remains possible, but it is secondary to the managed-session design center.

---

## 2. Scope and non-goals

### 2.1 In scope

This document covers:

- document and code retrieval for managed sessions,
- initial context resolution before or at the start of a managed step,
- session-initiated follow-up retrieval during a managed run,
- embedding-model-assisted semantic search against Qdrant,
- `ContextPack` publication and prompt/context delivery,
- direct and gateway retrieval transport,
- workspace overlay retrieval,
- retrieval budgeting, filtering, and observability,
- the relationship between RAG configuration and Provider Profiles.

### 2.2 Out of scope

This document does **not** define:

- the full ingest pipeline in detail,
- the full managed-session contract,
- the full Provider Profile schema,
- raw secret backend behavior,
- general long-term memory architecture outside the document/code retrieval lane,
- unrestricted direct database administration from inside managed sessions.

Managed sessions may initiate retrieval queries, but they should not receive unrestricted control-plane authority over vector infrastructure by default.

---

## 3. Architectural position in MoonMind

MoonMind owns context assembly. Managed sessions consume context that MoonMind resolves, publishes, and delivers through artifacts, refs, workspace materialization, and runtime-specific input injection.

For shared managed-agent contracts, `contextRefs` is the normative place to reference retrieval results, context packs, instruction bundles, and related artifacts. Large context bodies belong behind refs rather than being inlined into durable workflow payloads.

Accordingly, Workflow RAG is one **context plane** inside the broader managed-session context system:

- planning and prior run history may contribute context,
- skills may contribute context,
- task attachments may contribute context,
- **document/code retrieval contributes context via Workflow RAG**.

This document is specifically about that last plane.

---

## 4. Core model

### 4.1 Primary model: MoonMind-owned initial context resolution

The primary managed-session RAG flow is:

1. MoonMind receives the task or step instruction.
2. MoonMind resolves retrieval settings and retrieval scope.
3. MoonMind embeds the instruction or derived retrieval query.
4. MoonMind searches the vector index.
5. MoonMind builds a `ContextPack`.
6. MoonMind persists the pack as an artifact and/or publishes a ref.
7. MoonMind injects the retrieved context into the managed runtime’s next input surface.
8. The managed session consumes that context while performing the actual task.

This is the default model because it keeps durable truth, observability, and retrieval policy inside MoonMind rather than forcing the runtime session to be the primary owner of retrieval state.

### 4.2 Secondary model: session-initiated follow-up retrieval

After initial context injection, a managed session may determine that it needs more information.

When RAG is enabled, the managed session may issue additional retrieval requests using MoonMind-owned retrieval surfaces. Those requests may be used to:

- refine a search query,
- search for another symbol, file, design note, or API,
- compare multiple code paths,
- retrieve fresh overlay content after edits,
- narrow or broaden filters,
- request more or less context for the next turn.

This is the “agent can ask for more context” path. It is useful, but it is **secondary** to MoonMind-owned initial context assembly.

### 4.3 No additional general chat-model hop required for retrieval

Workflow RAG should continue to use embeddings and vector search only for retrieval. A general-purpose chat/completions model is not required merely to fetch context.

The managed runtime’s normal model still consumes the retrieved context afterward to do the actual task. But retrieval itself remains:

- embedding request,
- vector search,
- deterministic context packaging.

---

## 5. Current implementation

### 5.1 Retrieval path

The current lean retrieval layer lives under `moonmind/rag/`.

The retrieval path is:

1. `ContextRetrievalService` embeds the query using the configured embedding provider.
2. Qdrant is queried through the lean RAG client.
3. Results are formatted into a `ContextPack`.
4. `ContextPack.context_text` is published for injection into runtime input.

This retrieval path does not route through a separate generative LLM summarization hop.

### 5.2 Managed runtime integration today

Codex is the current reference managed-session implementation.

In the current Codex runtime strategy, `prepare_workspace()` invokes `ContextInjectionService` before the command is built. `ContextInjectionService` resolves retrieval, persists a context artifact under `artifacts/context/`, prepends the retrieved context to `instruction_ref`, and adds safety framing that treats retrieved text as untrusted reference data.

If retrieval is unavailable or skipped for allowed reasons, local workspace fallback search may be used instead.

### 5.3 Retrieval transports

Two retrieval transports exist today:

- **`direct`** — the worker or managed runtime environment performs embedding and talks to Qdrant directly.
- **`gateway`** — the worker or managed runtime calls a MoonMind retrieval gateway, and the gateway performs retrieval on its behalf.

`gateway` should be the preferred default when MoonMind owns the outbound retrieval path or when embedding credentials are not otherwise available inside the worker/session environment.

### 5.4 Overlay support

Workflow RAG supports workspace overlay retrieval so a managed run can retrieve content that has changed during the run and has not yet been folded into the canonical index.

Overlay data remains part of the retrieval plane, not a separate ad hoc runtime memory system.

---

## 6. Managed-session contract for RAG

### 6.1 Context belongs behind refs and artifacts

Managed-session contracts should reference retrieval results through `contextRefs` or artifact refs.

Large retrieved bodies should not be copied into durable workflow payloads. The authoritative durable surfaces remain:

- artifacts,
- bounded workflow metadata,
- execution/read models,
- session continuity artifacts.

### 6.2 Session state is a continuity cache, not durable truth

A managed session may remember prior retrieval results locally, but that local memory is a convenience cache.

Authoritative retrieval truth remains:

- the vector index,
- the persisted `ContextPack` artifact,
- bounded retrieval metadata,
- published refs and observability records.

If a session is reset or a new session epoch begins, MoonMind should be able to rebuild enough retrieval context from durable state.

### 6.3 Reset semantics

A session clear/reset is a continuity boundary, not a deletion of durable retrieval state.

After reset, MoonMind may:

- re-run retrieval for the new step,
- reattach the latest context pack ref,
- re-materialize the compact objective and instructions,
- let the session request additional retrieval again if needed.

---

## 7. Provider Profiles, embedding configuration, and retrieval ownership

### 7.1 Provider Profiles do not automatically solve retrieval credentials

Managed-session Provider Profiles exist to launch and shape the managed runtime against its provider. They are not, by themselves, the complete retrieval-credential model.

A profile such as `codex_default` may be OAuth-backed or runtime-specific and may not expose embedding credentials suitable for direct RAG execution.

### 7.2 Retrieval configuration is separate from managed-runtime launch configuration

Workflow RAG requires an embedding model configuration.

That embedding configuration may be satisfied in one of two ways:

1. **Direct mode**
   - the worker or session environment has embedding configuration available,
   - the environment can perform embedding and Qdrant search directly.

2. **Gateway mode**
   - the worker or session calls a MoonMind retrieval gateway,
   - MoonMind owns the embedding configuration and outbound retrieval calls.

Gateway mode is the recommended default when users only configure a managed runtime Provider Profile and do not separately provide embedding credentials to the runtime environment.

### 7.3 Preferred architecture

When MoonMind owns the outbound retrieval path, Workflow RAG should be **proxy-first/gateway-first**.

That keeps:

- embedding credentials out of most session containers,
- retrieval policy centralized,
- observability consistent,
- initial context assembly deterministic,
- runtime/provider launch concerns separate from retrieval concerns.

### 7.4 Practical rule

A managed runtime Provider Profile should not be treated as a generic source of embedding credentials by default.

Instead:

- runtime Provider Profiles launch the managed runtime,
- retrieval settings configure Workflow RAG,
- gateway transport bridges the two when needed.

---

## 8. Retrieval execution modes

### 8.1 Automatic pre-turn retrieval

This is the default managed-session path.

MoonMind resolves initial context before the managed session starts its substantive work on the step. The retrieved context is persisted, then injected into the next runtime input surface.

### 8.2 Explicit session retrieval requests

When RAG is enabled, a managed session may explicitly ask for more context during the run.

These retrieval requests should support at least:

- `query`
- `filters`
- `top_k`
- `overlay_policy`
- optional budget overrides within policy
- machine-readable output (`ContextPack` / JSON)
- text output (`context_text`) for immediate use in the next turn

This is the recommended meaning of “managed sessions can make arbitrary calls to the vector database.”

In practice, that means the session can issue arbitrary **authorized semantic retrieval queries** over the configured corpus, with embeddings produced on demand, rather than requiring a fixed one-shot prefetch only.

### 8.3 Preferred session-facing surface

The preferred session-facing surface is a MoonMind-owned retrieval tool or gateway call, not unrestricted raw database admin access.

That tool may be implemented as:

- `moonmind rag search`,
- a runtime adapter tool that calls the same retrieval layer,
- a gateway endpoint for managed-session retrieval requests,
- or an equivalent runtime-neutral retrieval capability.

### 8.4 Direct raw database access is optional, not the default contract

A deployment may choose to allow direct Qdrant access from a worker/session environment.

That is an implementation choice, not the primary managed-session contract. The default contract should be:

- the session may ask for retrieval,
- MoonMind performs or mediates retrieval,
- results come back as a `ContextPack` plus artifacts/refs.

---

## 9. Retrieval flow details

### 9.1 Initial retrieval flow

```text
Task/step instruction
  -> resolve retrieval settings and scope
  -> embed query
  -> vector search
  -> build ContextPack
  -> persist artifact / publish ref
  -> prepend or otherwise deliver retrieved context
  -> managed session executes with that context
```

### 9.2 Session-initiated retrieval flow

```text
Managed session decides more context is needed
  -> call MoonMind retrieval tool/gateway
  -> embed query
  -> vector search
  -> build ContextPack
  -> return context_text and JSON metadata
  -> optionally persist artifact / publish ref
  -> session continues work with the new context
```

### 9.3 Local fallback flow

If semantic retrieval is unavailable and policy allows degraded mode, MoonMind may use local workspace search fallback for initial context injection or explicit follow-up retrieval.

Fallback mode should be explicit in metadata and observability.

---

## 10. `ContextPack` contract

`ContextPack` is the shared retrieval result shape used by CLI and gateway flows.

At minimum, a pack contains:

- `context_text`
- `items`
- `filters`
- `budgets`
- `usage`
- `transport`
- `retrieved_at`
- `telemetry_id`

Each item should include enough provenance to explain what was retrieved, including at least:

- `source`
- `score`
- `text`
- `trust_class`
- offsets and chunk identity when available
- any bounded filter/payload metadata needed for operator understanding

### 10.1 Target contract expectations

The longer-term contract should continue to strengthen:

- provenance,
- trust class,
- overlay/canonical distinction,
- retrieval transport,
- truncation indicators,
- usage/budget reporting,
- compact artifact refs for durable publication.

### 10.2 Prompt safety

Retrieved context is reference data, not instructions.

Runtime injection must continue to preserve a safety boundary that tells the model to treat retrieved content as untrusted reference material and to prefer current repository state when retrieved content conflicts with the checked-out workspace.

---

## 11. Filters, scope, and “how much context” knobs

Workflow RAG should expose simple, high-value levers for retrieval amount and scope.

### 11.1 Primary knobs

- **`top_k` / `RAG_SIMILARITY_TOP_K`**
  - how many hits to retrieve before formatting.

- **`max_context_chars` / `RAG_MAX_CONTEXT_LENGTH_CHARS`**
  - how much retrieved text may be packed into `context_text`.

- **filters**
  - repo/workspace/run/job/tenant metadata used to constrain the search space.

### 11.2 Budget knobs

- **token budget**
  - constrains retrieval size before or during execution.

- **latency budget**
  - constrains slow or overly expensive retrieval requests.

### 11.3 Overlay knob

- **overlay policy**
  - whether workspace overlay content is included or skipped.

These knobs should be available both for automatic initial retrieval and for session-initiated follow-up retrieval, subject to policy.

---

## 12. Managed-session enablement rules

### 12.1 RAG enabled vs disabled

When RAG is disabled:

- MoonMind does not perform semantic retrieval,
- the managed session should not assume retrieval tools are available,
- any retrieval request should fail fast with a clear reason.

When RAG is enabled:

- MoonMind may perform automatic initial retrieval,
- the managed session may request additional retrieval,
- retrieved context should be published with artifacts/refs and observability metadata.

### 12.2 Session-facing capability signal

Managed runtimes should receive an explicit capability signal when RAG is enabled.

That signal should explain:

- that retrieval is available,
- how to request more context,
- that retrieved content is reference data,
- any relevant budgets or scope constraints.

This capability signal should be runtime-neutral in concept even if the exact prompt/tooling surface differs by runtime.

---

## 13. Observability and evidence

Every retrieval operation should leave durable evidence sufficient for debugging and operator trust.

At minimum, observability should record:

- whether retrieval ran,
- whether it was automatic or session-initiated,
- transport used (`direct`, `gateway`, `local_fallback`),
- filters applied,
- number of items returned,
- token/latency usage and budgets,
- whether context was truncated,
- artifact path or context ref,
- degraded mode / fallback reason when applicable.

UI badges and timeline rendering are consumers of this evidence, not the evidence itself.

---

## 14. Security and trust rules

### 14.1 No raw secrets in workflow payloads

Workflow RAG must not place raw provider keys, OAuth tokens, or generated secret-bearing config bodies into durable workflow payloads or artifacts.

### 14.2 Retrieval should be bounded by policy

Even when managed sessions may issue arbitrary retrieval queries, those queries remain bounded by:

- authorized corpus scope,
- filters,
- budgets,
- transport policy,
- provider/secret policy,
- audit/observability requirements.

### 14.3 Gateway-first preference

When MoonMind owns the outbound retrieval path, prefer gateway/proxy execution so that most session containers do not need direct embedding secrets or direct vector-store credentials.

### 14.4 Trust boundary for retrieved text

Retrieved text may contain stale, conflicting, or malicious content.

Prompt injection and runtime instructions must continue to:

- treat retrieved text as untrusted,
- avoid executing instructions embedded in retrieved content,
- prefer the current workspace state over retrieved stale copies when they conflict.

---

## 15. Runtime rollout

### 15.1 Codex is the reference implementation today

Codex is the current live managed-session reference implementation for Workflow RAG.

### 15.2 Future runtimes should implement the same shared model

Claude Code, Gemini CLI, and future runtimes should support the same retrieval contract:

- MoonMind-owned initial context resolution,
- artifact/ref-backed retrieval publication,
- session-initiated follow-up retrieval when RAG is enabled,
- the same `ContextPack` and observability model,
- runtime-specific delivery details only at the adapter layer.

No runtime should invent a separate top-level RAG architecture.

---

## 16. Environment and settings

The exact configuration surface may evolve, but the current retrieval settings include the following concepts:

| Setting | Purpose |
|---|---|
| `DEFAULT_EMBEDDING_PROVIDER` | Select embedding provider |
| provider-specific embedding model setting | Select embedding model |
| `QDRANT_HOST` / `QDRANT_PORT` / `QDRANT_URL` | Vector store location |
| `QDRANT_API_KEY` | Vector store auth when required |
| `VECTOR_STORE_COLLECTION_NAME` | Retrieval collection |
| `RAG_SIMILARITY_TOP_K` | Default retrieval count |
| `RAG_MAX_CONTEXT_LENGTH_CHARS` | Injected context size cap |
| `RAG_OVERLAY_MODE` | Overlay storage mode |
| `MOONMIND_RETRIEVAL_URL` | Gateway transport endpoint |
| `RAG_QUERY_TOKEN_BUDGET` | Retrieval token budget |
| `RAG_LATENCY_BUDGET_MS` | Retrieval latency budget |
| `MOONMIND_RAG_AUTO_CONTEXT` | Automatic initial retrieval enablement |

Mission Control UI exposure for these settings is a separate concern from the retrieval contract itself.

---

## 17. Recommended desired-state statement

MoonMind’s desired Workflow RAG model is:

- **embedding-first and retrieval-only** for context resolution,
- **managed-session centered** rather than chat-loop centered,
- **MoonMind-owned** for initial context assembly,
- **artifact/ref-backed** for durable truth,
- **gateway-first** when MoonMind owns the outbound retrieval path,
- **session-initiated follow-up capable** when RAG is enabled,
- **bounded by policy, filters, and budgets**,
- **consistent across runtimes** through the shared managed-session contract.

In that desired state:

- initial context is resolved before or at step start,
- managed sessions can request additional semantic retrieval as needed,
- retrieval still uses embeddings plus vector search rather than an extra general LLM hop,
- Provider Profiles remain focused on runtime launch and provider shaping,
- and Workflow RAG becomes the document/code retrieval plane inside the broader managed-session context system.
