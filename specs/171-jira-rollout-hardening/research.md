# Research: Jira Create-Page Rollout Hardening

## Decision: Reuse MoonMind's trusted Jira boundary for browser reads

**Rationale**: The Create-page browser must never receive raw Jira credentials or call Jira directly. Existing server-side Jira auth/client code already centralizes SecretRef resolution, auth-mode validation, timeouts, retries, redaction, and provider error mapping. A browser-focused read layer can reuse that boundary while returning Create-page-ready models.

**Alternatives considered**:

- Browser-to-Jira calls with user-provided credentials: rejected because it creates a new credential path and bypasses MoonMind policy enforcement.
- Reusing mutation-oriented Jira tool responses directly in the UI: rejected because agent tool contracts are intentionally narrow and not shaped for board browsing.

## Decision: Gate the Create-page Jira UI independently from backend Jira tooling

**Rationale**: Operators may want trusted Jira tools available to agents without exposing Jira browsing controls in Mission Control. Runtime config should publish Jira UI sources and `system.jiraIntegration` only when the Create-page rollout is enabled.

**Alternatives considered**:

- Show UI whenever Jira tooling is configured: rejected because backend tooling enablement and browser UI exposure have different rollout and security implications.
- Hardcode browser controls client-side: rejected because operators need runtime configurability without image rebuilds.

## Decision: Keep one shared Jira browser surface in the Create page

**Rationale**: The desired behavior targets two existing authoring surfaces: preset objective and step instructions. One shared browser avoids duplicated state, duplicate data fetching, and inconsistent import behavior. The browser opens with a target selected and imports only on explicit operator action.

**Alternatives considered**:

- Embed independent Jira browsers beside every field: rejected because it multiplies network state and makes one-open-at-a-time behavior harder.
- Treat Jira issues as a separate task model: rejected because the Create page's existing step/preset model remains the canonical task-composition surface.

## Decision: Keep Jira provenance local for the MVP

**Rationale**: The task submission path is already stable and includes objective resolution, dependencies, artifact fallback, runtime configuration, and scheduling. Jira provenance is useful for operator clarity but has no current downstream execution consumer, so it should remain advisory UI state for the MVP.

**Alternatives considered**:

- Persist Jira provenance in the task payload immediately: rejected because it expands the submission contract without a clear consumer.
- Omit provenance entirely: rejected because users need to see which Jira issue was copied into a field.

## Decision: Preserve explicit preset reapply semantics

**Rationale**: Applied preset steps are expanded blueprints, not live links. If Jira import changes preset objective text after preset application, existing steps should not be silently rewritten. A reapply-needed message preserves user control and keeps mutations explicit.

**Alternatives considered**:

- Automatically regenerate preset-derived steps on import: rejected because it can overwrite operator edits unexpectedly.
- Ignore preset dirty state after Jira import: rejected because users would not understand why existing expanded steps no longer match preset inputs.

## Decision: Validate through focused unit and UI tests

**Rationale**: The feature crosses runtime config, Jira integration boundaries, API routing, frontend state, import semantics, and failure handling. Focused tests at each boundary provide coverage without requiring live Jira credentials.

**Alternatives considered**:

- Provider verification only: rejected because live Jira credentials are not required for merge confidence and would make CI brittle.
- Frontend-only validation: rejected because trusted server-side policy, normalization, and redaction are core requirements.
