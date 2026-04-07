# Research: codex-managed-session-plane-phase11

## Key Decisions

### 1. Reuse the Phase 9 session projection instead of inventing a UI-only session state model

The existing task-run projection already exposes the current session epoch plus latest continuity and control refs. Phase 11 should consume that read model directly so the UI stays artifact-first.

### 2. Do not reuse the old generic `SendMessage` path for Codex managed sessions

The current task-level message forwarding path only reaches Jules. Codex managed-session follow-up needs a dedicated session-plane control path so the UI does not offer a misleading button that never reaches the session container.

### 3. Put follow-up/reset execution in `MoonMind.AgentSession`

`MoonMind.AgentSession` already owns task-scoped session identity. Executing follow-up/reset through that workflow keeps the session plane durable and consistent with the Phase 2 ownership model instead of scattering session mutations across UI or root-task shortcuts.

### 4. Keep cancellation task-scoped, not session-shell-scoped

Phase 11 only needs one cancel affordance in the panel. Reusing the existing task cancellation route keeps the operator model coherent and avoids adding a second, competing cancellation semantics for the same task detail page.
