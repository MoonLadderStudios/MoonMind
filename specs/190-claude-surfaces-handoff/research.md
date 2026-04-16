# Research: Claude Surfaces Handoff

## Surface Binding Extension

Decision: Extend `ClaudeSurfaceBinding` with bounded capabilities and an optional last-seen timestamp while preserving existing defaults.

Rationale: MM-348 requires durable bindings that can represent primary and projection surfaces, connection state, interactivity, and capabilities without introducing persistent storage. Extending the existing model preserves MM-342 compatibility and keeps validation at the schema boundary.

Alternatives considered: Add a separate `ClaudeSurfaceAttachment` model. Rejected because it would duplicate the existing `ClaudeSurfaceBinding` contract and create two surface nouns.

## Surface Lifecycle Events

Decision: Add a compact `ClaudeSurfaceLifecycleEvent` model with normalized event names for attach, connect, disconnect, reconnect, detach, resume, and handoff creation.

Rationale: The source design requires normalized surface events, and downstream workflow/audit consumers need bounded identity fields rather than free-form logs.

Alternatives considered: Reuse `ClaudeManagedWorkItem` event names. Rejected because surface lifecycle can happen outside a turn work item and needs explicit surface/handoff lineage fields.

## Handoff Seed References

Decision: Store handoff summaries as bounded `handoffSeedArtifactRefs` on the destination session and event metadata, not as embedded summary payloads.

Rationale: The Jira brief asks how cloud handoff summaries should be versioned and audited. Artifact refs provide a safe default for versionable lineage without deciding full payload retention policy in this story.

Alternatives considered: Embed the summary text in workflow history or the session record. Rejected because large content does not belong in compact workflow payloads and would blur artifact ownership.

## Test Strategy

Decision: Use focused schema unit tests plus an integration-style fixture flow test.

Rationale: This story defines runtime contracts at the schema boundary and does not require live Claude provider credentials. Unit tests cover invariants and failure cases; integration-style tests prove the representative attach, disconnect, reconnect, resume, and handoff flow.

Alternatives considered: Provider verification tests. Rejected because the story is contract-first and live provider behavior is outside scope.
