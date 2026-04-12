# Research: Jira UI Runtime Config

## Decision: Use the existing dashboard runtime config builder

**Rationale**: The Create page already receives server-generated runtime configuration through the dashboard boot payload. Adding Jira discovery to the same runtime config path keeps capability discovery centralized and avoids a parallel browser configuration mechanism.

**Alternatives considered**:

- Add a standalone Jira config endpoint: rejected because Phase 1 only needs boot-time discovery and would create another configuration surface before the browser API exists.
- Hardcode Jira endpoints in the frontend: rejected because the desired-state design requires server-owned runtime configuration and MoonMind-owned API boundaries.

## Decision: Gate Jira UI exposure with a Create-page-specific feature flag

**Rationale**: Backend Jira tooling and browser Jira controls serve different threat models. Operators must be able to enable trusted server-side Jira tools without exposing Jira browser entry points to Create page users.

**Alternatives considered**:

- Reuse backend Jira tool enablement: rejected because it would couple server-side automation permissions to browser UI rollout.
- Always expose Jira sources with disabled UI state elsewhere: rejected because omission is the safest disabled contract and easiest for existing consumers to ignore.

## Decision: Publish only MoonMind-owned endpoint templates

**Rationale**: Browser clients must not call Jira directly or receive raw Jira credentials. Endpoint templates keep the browser coupled to MoonMind APIs while leaving actual Jira auth, policy, and redaction to trusted server-side code in later phases.

**Alternatives considered**:

- Include Jira base URLs or connection credentials: rejected by the Create page security contract.
- Include raw Jira query details in runtime config: rejected because Phase 1 should define UI discovery only, not browser-side Jira knowledge.

## Decision: Model defaults as browser-safe strings and booleans

**Rationale**: The default project key, default board ID, and session-memory permission are sufficient for later browser preselection without introducing persistent state or new database entities.

**Alternatives considered**:

- Persist last-selected Jira board in backend storage: rejected as outside Phase 1 and unnecessary for session-only behavior.
- Require defaults before enabling Jira UI: rejected because empty defaults are a valid no-preselection state.

## Decision: Validate with focused runtime-config unit tests

**Rationale**: The highest-risk behavior is contract shape and rollout gating. Unit tests can directly exercise `build_runtime_config()` with feature settings patched on and off, giving deterministic coverage without external Jira dependencies.

**Alternatives considered**:

- Frontend tests: deferred because no frontend Jira UI is introduced in Phase 1.
- Integration tests against Jira: rejected because Phase 1 does not contact Jira and should remain hermetic.
