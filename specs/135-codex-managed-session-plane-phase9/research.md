# Research: Codex Managed Session Plane Phase 9

## Decision 1: Use the durable managed-session record as the projection seed

- **Decision**: Build the Phase 9 projection from `CodexManagedSessionRecord` plus artifact metadata lookups for the refs already persisted in Phase 8.
- **Rationale**: The durable session record already stores the latest runtime and continuity refs, which is sufficient for a minimal continuity read model.
- **Alternatives considered**:
  - Live session-controller queries: rejected because Phase 9 must stay artifact-first and durable-state-first.
  - A new projection persistence layer: rejected because the MVP can be computed cheaply at request time.

## Decision 2: Reuse task-run authorization, then read artifacts as a service principal

- **Decision**: Authorize access at the task-run API boundary and use a service principal for artifact metadata resolution inside the projection builder.
- **Rationale**: Managed-session artifacts are system-produced; task-run ownership is the operator-facing authorization boundary that already fits Mission Control.
- **Alternatives considered**:
  - Enforce artifact-by-artifact end-user ownership: rejected because it would hide system-generated artifacts that belong to the user-visible task.

## Decision 3: Return grouped latest artifacts, not a full historical timeline

- **Decision**: The Phase 9 response returns the latest durable epoch and grouped latest artifacts for runtime and continuity/control categories.
- **Rationale**: The user asked for a minimal projection API; later phases can extend this to broader timeline/history queries if needed.
- **Alternatives considered**:
  - Cross-epoch timeline reconstruction: rejected because it adds query complexity before the first continuity UI exists.
