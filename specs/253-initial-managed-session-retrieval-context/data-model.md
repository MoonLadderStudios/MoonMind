# Data Model: Initial Managed-Session Retrieval Context

## Entities

### Retrieval Settings

The bounded runtime configuration MoonMind resolves before managed-session retrieval begins.

Fields:
- Run identity: run/job identifiers used for retrieval telemetry and overlay selection.
- Filters: repository and other retrieval metadata filters derived from the execution request.
- Transport: direct or gateway retrieval mode.
- Budgets: optional token and latency budgets.
- Overlay policy: whether run-local overlay retrieval is included.

Validation rules:
- Retrieval must be executable under the resolved runtime settings before MoonMind attempts initial search.
- Positive token and latency budgets are normalized; invalid values are ignored rather than silently becoming negative/zero limits.
- Repository filters are normalized before retrieval search is executed.

### ContextPack

The shared retrieval result package MoonMind builds from embedding-backed search results.

Fields:
- Items: retrieved snippets with source, score, trust class, and optional offsets/payload metadata.
- Filters: normalized retrieval filters used for the search.
- Budgets: normalized retrieval budgets used for the search.
- Usage: retrieval usage and latency metrics.
- Transport: retrieval transport used for the pack.
- Context text: compact rendered retrieval text for runtime injection.
- Retrieved-at timestamp and telemetry identifier.

Validation rules:
- Context text must remain compact and derived from the retrieved items rather than becoming an unbounded payload dump.
- Retrieved items keep trust metadata so runtime framing can treat them as reference data instead of instructions.

### Prompt Context Resolution

The runtime-facing result of preparing a managed-session instruction with initial retrieval context.

Fields:
- Final instruction text presented to the runtime.
- Item count for retrieved context.
- Artifact path when MoonMind persisted a context pack for the step.

Validation rules:
- If retrieval yields no items, the original instruction may remain unchanged while artifact metadata can still be recorded.
- If retrieval yields items, the returned instruction must include the untrusted-retrieved-text framing and the original instruction body.

### Retrieved Context Artifact

The durable task-scoped artifact that stores the serialized context pack used for initial managed-session startup.

Fields:
- Artifact directory under the workspace.
- Stable file name derived from run identity and instruction digest.
- Serialized context pack JSON content.

Validation rules:
- Artifact publication must occur under the task workspace artifacts directory.
- The artifact is the durable startup evidence for retrieval output and must stay reusable without requiring a replay of the original runtime session.

## Relationships

- Retrieval Settings drive one initial retrieval request.
- One retrieval request produces one ContextPack.
- One ContextPack may be published as one Retrieved Context Artifact for a managed-session step.
- Prompt Context Resolution references the published artifact path and composes the runtime instruction that consumes the retrieved context.
- The same Prompt Context Resolution contract is now consumed by both Codex and Claude workspace preparation paths.

## State Transitions

1. Retrieval settings resolved.
2. Retrieval request evaluated for executability and transport.
3. Embedding-backed search executed and ContextPack built.
4. ContextPack published behind a task-scoped artifact.
5. Runtime instruction composed with untrusted retrieved context framing.
6. Managed runtime receives the prepared instruction at startup.

## Invariants

- Initial retrieval happens before managed runtime task execution begins.
- Retrieved context is durable and observable through artifact-backed output rather than only transient runtime memory.
- Retrieved text remains untrusted reference data in the runtime instruction.
- The initial retrieval contract remains reusable across managed-session runtimes even if startup implementations differ.
