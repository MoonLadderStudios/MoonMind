# Contract: Claude Surfaces Handoff

## Exported Schema Surface

The managed-session schema package exposes:

- `ClaudeSurfaceCapability`
- `ClaudeSurfaceLifecycleEventName`
- `ClaudeExecutionSecurityMode`
- `ClaudeSurfaceLifecycleEvent`
- `ClaudeSurfaceHandoffFixtureFlow`
- `build_claude_surface_handoff_fixture_flow`
- `classify_claude_execution_security_mode`
- `CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES`

## Session Operations

### `ClaudeManagedSession.with_surface_binding(...) -> ClaudeManagedSession`

Adds or updates one surface binding.

Required behavior:
- Preserves `session_id`.
- Preserves `execution_owner`.
- Allows one primary binding and multiple remote projections.
- Rejects a second primary binding with a different surface id.
- Updates `updated_at`.

### `ClaudeManagedSession.with_surface_connection_state(...) -> ClaudeManagedSession`

Updates an existing surface binding connection state.

Required behavior:
- Preserves `session_id`.
- Preserves `execution_owner`.
- Does not force session state to `failed`.
- Rejects unknown surface ids.

### `ClaudeManagedSession.resume_on_surface(...) -> ClaudeManagedSession`

Moves the primary surface for the same execution owner.

Required behavior:
- Preserves canonical `session_id`.
- Preserves `execution_owner`.
- Updates `primary_surface`.
- Produces exactly one primary surface binding.
- Does not set handoff lineage.

### `ClaudeManagedSession.cloud_handoff(...) -> ClaudeManagedSession`

Creates a distinct cloud-owned destination session.

Required behavior:
- Requires a distinct destination session id.
- Sets `execution_owner = anthropic_cloud_vm`.
- Sets `projection_mode = handoff`.
- Sets `handoff_from_session_id` to the source session.
- Carries bounded `handoff_seed_artifact_refs` when provided.

## Event Contract

`ClaudeSurfaceLifecycleEvent` accepts only:

- `surface.attached`
- `surface.connected`
- `surface.disconnected`
- `surface.reconnecting`
- `surface.detached`
- `surface.resumed`
- `surface.handoff.created`

Surface-scoped events require `surface_id`. Handoff events require source and destination session identifiers and may carry seed artifact refs.
