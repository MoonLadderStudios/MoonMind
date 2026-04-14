# Research: Codex Managed Session Plane Phase 7

- The desired-state reset contract already exists in [`docs/ManagedAgents/CodexCliManagedSessions.md`](../../docs/ManagedAgents/CodexCliManagedSessions.md): `clear_session` must write `session.control_event`, write `session.reset_boundary`, then increment `session_epoch`.
- Phase 6 already introduced the durable `CodexManagedSessionRecord` fields needed for this slice: `latest_control_event_ref` and `latest_checkpoint_ref`.
- The current implementation gap is limited to the service boundary: the remote runtime clears the session and appends spool text, but the controller/supervisor never materialize durable reset artifacts.
