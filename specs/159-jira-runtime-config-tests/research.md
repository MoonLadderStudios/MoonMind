# Research: Jira Runtime Config Tests

## Decision: Use the existing dashboard runtime config builder

**Rationale**: The Create page already receives server-generated runtime configuration through the dashboard boot payload. Publishing Jira discovery through that same path keeps capability discovery centralized and avoids a parallel configuration mechanism.

**Alternatives considered**:

- Add a standalone Jira UI discovery endpoint: rejected because Phase 3 only needs boot-time discovery and the existing runtime config builder already owns comparable Create-page sources and system flags.
- Hardcode Jira endpoints in frontend code: rejected because the desired-state Create page design requires server-owned runtime configuration and MoonMind-owned API boundaries.

## Decision: Keep Jira UI rollout separate from backend Jira tool enablement

**Rationale**: Trusted backend Jira tooling and browser-visible Jira controls serve different security and rollout purposes. Operators must be able to enable server-side Jira automation without exposing Create-page Jira browser entry points.

**Alternatives considered**:

- Reuse backend Jira enabled/tool-enabled settings: rejected because that couples trusted automation capability to browser UI exposure.
- Always expose Jira sources and rely on frontend hiding: rejected because omission is a safer disabled contract and keeps existing consumers unaffected.

## Decision: Publish only MoonMind-owned endpoint templates

**Rationale**: Browser clients must not call Jira directly or receive raw Jira credentials. Runtime config endpoint templates should point to MoonMind APIs so actual Jira auth, policy, and redaction remain server-side in later phases.

**Alternatives considered**:

- Include Jira base URLs or connection auth hints: rejected by the Create page security contract.
- Include raw Jira query metadata in runtime config: rejected because Phase 3 is discovery only, not browser-side Jira semantics.

## Decision: Model defaults as browser-safe strings and booleans

**Rationale**: Default project key, default board ID, and session-memory permission are sufficient for later browser preselection without introducing persistence or new data ownership questions.

**Alternatives considered**:

- Persist last-selected Jira board in backend storage: rejected as outside Phase 3 and unnecessary for session-only preference restoration.
- Require defaults before enabling Jira UI: rejected because an enabled browser with no preselected project or board is a valid deployment state.

## Decision: Validate with focused runtime-config unit tests

**Rationale**: The highest-risk behavior is contract shape and rollout gating. Unit tests can directly exercise runtime config generation with settings patched on and off, giving deterministic coverage without external Jira dependencies.

**Alternatives considered**:

- Frontend tests: deferred because this phase introduces no Jira browser UI.
- Integration tests against Jira: rejected because this phase must remain hermetic and does not contact Jira.
