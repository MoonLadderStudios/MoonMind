# Data Model: Managed-Session Retrieval Durability Boundaries

## Entities

### Durable Retrieval Artifact

The authoritative persisted retrieval output for a managed-session step.

Fields:
- Artifact path or ref for the serialized `ContextPack`
- Retrieval transport and item-count metadata
- Retrieval timestamp and telemetry identity
- Workspace/run correlation needed to recover the artifact later

Validation rules:
- Large retrieved bodies remain inside the artifact content rather than durable workflow metadata.
- The artifact must stay usable after a session reset or new session epoch.
- The artifact path/ref must be relative or otherwise bounded for runtime-facing metadata.

### Retrieval Continuity Metadata

Compact metadata that points from managed-session/runtime state to durable retrieval truth.

Fields:
- Latest retrieved context artifact ref or path
- Transport used for publication
- Retrieved item count
- Optional latest context-pack reference chosen for recovery

Validation rules:
- Metadata must be compact and must not embed full retrieved bodies.
- Metadata may support reattach or rerun decisions for the next step, but it cannot become the sole durable source of truth.

### Managed Session Epoch

The continuity interval for one logical managed session.

Fields:
- Logical session id
- Session epoch number
- Runtime identifier
- Session status/readiness
- Workspace and artifact-spool paths

Validation rules:
- Advancing the epoch is a continuity boundary, not deletion of durable retrieval truth.
- Reset or replacement may discard session-local cache state while durable retrieval artifacts remain authoritative.

### Session Continuity Cache

Runtime-local retrieval memory retained for convenience inside the session container or runtime process.

Fields:
- Runtime-local retrieval memory or prompt state
- Optional runtime-native thread/session state
- Derived in-session copies of previously published retrieval text

Validation rules:
- The continuity cache may improve reuse and performance but cannot be authoritative.
- Losing the continuity cache must not erase durable retrieval evidence or make the next step unrecoverable.

### Recovery Decision

The bounded recovery choice MoonMind applies after reset or a new session epoch.

Fields:
- Recovery mode: rerun retrieval or reattach latest durable context ref
- Target artifact/ref when reattaching
- Step or run correlation for the next execution boundary

Validation rules:
- Recovery behavior must be deterministic and observable.
- Recovery must consume durable retrieval truth rather than session-local cache state.

## Relationships

- One managed-session step may publish one Durable Retrieval Artifact.
- Retrieval Continuity Metadata points to the latest Durable Retrieval Artifact for the logical session/run.
- One Managed Session Epoch may end and a later epoch may recover from the same Durable Retrieval Artifact.
- Session Continuity Cache may contain copies of retrieved context, but Recovery Decision must be based on durable artifact/ref-backed truth.

## State Transitions

1. Retrieval context is resolved and a durable artifact/ref is published.
2. Compact continuity metadata records the latest durable retrieval reference.
3. The managed session continues work using runtime-local cache as a convenience only.
4. A reset or new session epoch begins.
5. MoonMind recovers retrieval context by rerunning retrieval or reattaching the latest durable reference.
6. The next step executes against the recovered durable retrieval truth.

## Invariants

- Durable retrieval truth must remain recoverable without session-local cache state.
- Large retrieved bodies remain behind artifacts/refs instead of durable workflow payloads.
- Reset and epoch replacement preserve durable retrieval evidence.
- Recovery behavior is runtime-neutral at the MoonMind contract boundary.
