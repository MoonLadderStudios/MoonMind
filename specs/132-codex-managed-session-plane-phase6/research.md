# Research: Codex Managed Session Plane Phase 6

## Decisions

### 1. Reuse `RuntimeLogStreamer`

- Reuse the existing managed-run `RuntimeLogStreamer` for stdout/stderr artifact creation and diagnostics bundle generation.
- Rationale: it already matches MoonMind's artifact-first observability model and keeps session logs aligned with run logs.

### 2. Supervise Host-Visible Spool Files

- Supervise append-only stdout/stderr spool files under the mounted session artifact path instead of depending on a live interactive session.
- Rationale: the spool path is restart-safe, container-visible, and compatible with later session continuity projections.

### 3. Durable Session Record As The Summary Source

- `fetch_session_summary` and `publish_session_artifacts` should read from the durable session record first, using the container only for control-plane actions.
- Rationale: this keeps continuity artifact-first and avoids making the session container the system of record.

### 4. Reconcile In Worker Startup

- Reconciliation belongs in worker bootstrap beside managed-run reconciliation, not in workflow code.
- Rationale: the worker owns the concrete controller/supervisor lifecycle and can recover active records after restart without changing workflow determinism.
