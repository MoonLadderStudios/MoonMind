# Workflow Context Assembly — Managed Session Context

Omnigent retrieval scope and budgets come from the bound
[policy snapshot](../Omnigent/PolicyAuthority.md).

**Status:** Implemented
**Owners:** MoonMind Engineering
**Last Updated:** 2026-09-09

> **See also:**
> - [ManagedAgentArchitecture.md](../ManagedAgents/ManagedAgentArchitecture.md)
> - [SharedManagedAgentAbstractions.md](../ManagedAgents/SharedManagedAgentAbstractions.md)
> - [ProviderProfiles.md](../Security/ProviderProfiles.md)
> - [ManifestIngestDesign.md](./ManifestIngestDesign.md)
> - [LlamaIndexManifestSystem.md](./LlamaIndexManifestSystem.md)

## 1. Summary

This document defines how **exact context assembly** works for **managed sessions** in MoonMind.

MoonMind’s managed-agent architecture is **managed-session first**. A managed session is the runtime-owned work session, but MoonMind remains the owner of durable context assembly, artifact publication, and orchestration. Workflow context assembly is therefore **not** a chat-style “agent remembers to search” feature and **not** semantic recall over a managed vector index. Its job is to let MoonMind assemble explicitly authorized context, publish that context as durable artifacts and refs, and deliver it into managed sessions at the right step boundaries.

MoonMind is vector-free: there is no MoonMind-managed vector database,
embedding service, collection, or retrieval index. Ordinary workflows and chat
need no vector configuration. Explicit vector requirements (`rag`,
`followUpRetrieval`, collection/overlay administration, embedding credentials)
are retired and rejected before execution. Historical retrieval details and
artifacts remain readable; they are not live capabilities.

The assembly path is intentionally lean:

**(resolve authorized sources) → (build context bundle) → (inject or publish context)**

A managed session consumes MoonMind-resolved context while performing the
actual work. It must not invent a separate runtime-specific retrieval model.

This document focuses on **managed-session usage**. Ad hoc or conversational
context requests remain possible, but they are secondary to the managed-session
design center.

---

## 2. Scope and non-goals

### 2.1 In scope

This document covers:

- exact context assembly for managed sessions,
- initial context resolution before or at the start of a managed step,
- `ContextPack` publication and prompt/context delivery,
- context budgeting, filtering, and observability,
- the relationship between context configuration and Provider Profiles.

### 2.2 Out of scope

This document does **not** define:

- semantic search over a managed vector index (retired),
- embedding-model configuration or collection administration (retired),
- the full manifest ingest pipeline in detail,
- the full managed-session contract,
- the full Provider Profile schema,
- raw secret backend behavior,
- general long-term memory architecture outside the exact-context lane,
- unrestricted direct database administration from inside managed sessions.

Managed sessions may consume MoonMind-resolved context, but they do not receive
control-plane authority over infrastructure by default.

---

## 3. Architectural position in MoonMind

MoonMind owns context assembly. Managed sessions consume context that MoonMind resolves, publishes, and delivers through artifacts, refs, workspace materialization, and runtime-specific input injection.

For shared managed-agent contracts, `contextRefs` is the normative place to reference context packs, instruction bundles, and related artifacts. Large context bodies belong behind refs rather than being inlined into durable workflow payloads.

Accordingly, workflow context assembly is one **context plane** inside the broader managed-session context system:

- planning and prior run history may contribute context,
- skills may contribute context,
- workflow attachments may contribute context,
- **explicitly authorized sources contribute context via workflow context assembly**.

This document is specifically about that last plane.

---

## 4. Core model

### 4.1 Primary model: MoonMind-owned initial context resolution

The primary managed-session context flow is:

1. MoonMind receives the workflow or step instruction.
2. MoonMind resolves authorized context sources and scope.
3. MoonMind loads explicitly referenced artifacts, attachments, history, and skill context.
4. MoonMind builds a `ContextPack`.
5. MoonMind persists the pack as an artifact and/or publishes a ref.
6. MoonMind injects the context into the managed runtime’s next input surface.
7. The managed session consumes that context while performing the actual work.

This is the default model because it keeps durable truth, observability, and context policy inside MoonMind rather than forcing the runtime session to be the primary owner of context state.

### 4.2 No session-invented retrieval model

After initial context injection, a managed session works with what MoonMind
resolved. Authored `rag` / `followUpRetrieval` policy is retired: new writes
carry no vector behavior, and explicit vector requirements fail with an
actionable validation error before Temporal start, host launch, paid work, or
external mutation. Absent, empty, or disabled values pass so normal authoring
needs no vector settings.

### 4.3 No additional general chat-model hop required for assembly

Context assembly itself requires no general-purpose chat/completions model hop.
The managed runtime’s normal model consumes the assembled context afterward to
do the actual work.

---

## 5. ContextPack contract

A `ContextPack` is a bounded, budgeted bundle of exactly-resolved context:

- `text`: the resolved content (bounded per item and in total),
- `source`: which authorized source produced it,
- `trust_class`: raw, derived, or approved,
- `provenance`: run, artifact, commit, or doc refs backing it,
- `recency`: when it was produced or last verified,
- `token_cost`: its budget weight.

Large bodies travel behind `contextRefs`; durable workflow payloads carry refs,
not inlined corpora.

---

## 6. Budgeting, filtering, and observability

Every assembly request carries explicit bounds:

- which tenant, repository, workflow, user, and security scope applies,
- which source classes are trusted,
- token and result budgets,
- redaction, retention, revocation, and deletion policy.

Assembly publishes durable evidence through the artifact system: what was
resolved, from which sources, under which budgets, and with which provenance.
Diagnostics stay compact; full content belongs in artifacts.

---

## 7. Provider Profiles and context ownership

A managed runtime Provider Profile resolves execution configuration; it is not
a generic source of retrieval credentials. MoonMind owns the outbound context
path: most session containers need no special context credentials because
MoonMind resolves and delivers context before the step runs.

---

## 8. Historical note (non-active)

Earlier revisions of this document described embedding-model-assisted semantic
search against a MoonMind-managed Qdrant service. That capability is retired:
new authoring must not reference it, and explicit references are rejected
before execution. Mentions of that history in old workflow payloads, preserved
artifacts, replay fixtures, and negative-test assertions remain readable as
historical evidence only.
