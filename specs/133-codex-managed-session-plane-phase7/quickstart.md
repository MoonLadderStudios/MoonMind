# Quickstart: Codex Managed Session Plane Phase 7

1. Launch a managed Codex session through the existing Phase 4/5/6 path.
2. Call `agent_runtime.clear_session` with a new `threadId`.
3. Call `agent_runtime.fetch_session_summary` and verify `latestControlEventRef` and `latestCheckpointRef` are populated.
4. Inspect the managed session artifact root and verify the epoch-specific `session.control_event` and `session.reset_boundary` artifacts exist.
