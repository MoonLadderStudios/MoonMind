# Research: Live Logs Session Plane Producer

## Decision 1: Reuse the Phase 1 event kinds instead of adding a second session-event model

- **Decision**: Keep `RunObservabilityEvent` as the sole event model and use the existing session/publication kinds already added in Phase 1.
- **Rationale**: Phase 2 is about producing missing rows, not redesigning the contract again.
- **Alternatives considered**:
  - Add dedicated action kinds for every control verb. Rejected because Phase 1 already shipped lifecycle/publication kinds and a generic `system_annotation` is sufficient for steer-only control facts.

## Decision 2: Make session-event publication best-effort at both controller and supervisor boundaries

- **Decision**: Catch and log exceptions during session-event publication so successful control actions and artifact publication continue.
- **Rationale**: The plan explicitly requires publishing failures to avoid breaking runtime control or durable artifact persistence.
- **Alternatives considered**:
  - Let event publication failures fail the control action. Rejected because it incorrectly makes observability authoritative over runtime state changes.

## Decision 3: Signal `start_session` and `resume_session` from the adapter when launch/reuse is decided

- **Decision**: Mirror `start_session` and `resume_session` through the adapter’s existing control signal path at the moment `_ensure_remote_session()` chooses launch vs reuse.
- **Rationale**: The adapter already owns that decision and is the correct place to keep workflow/session projection state consistent with the actual runtime path.
- **Alternatives considered**:
  - Infer resume from generic `session_status` polling later. Rejected because it loses the explicit control intent and is harder to keep deterministic.

## Decision 4: Emit publication rows from `ManagedSessionSupervisor._publish_record()`

- **Decision**: Emit `summary_published` and `checkpoint_published` immediately after the summary/checkpoint artifacts are written.
- **Rationale**: That method already owns the authoritative publication refs and the run-global observability journal.
- **Alternatives considered**:
  - Emit publication rows from the controller after `publish_session_artifacts()`. Rejected because the controller may return an already-published record and does not own artifact creation.
