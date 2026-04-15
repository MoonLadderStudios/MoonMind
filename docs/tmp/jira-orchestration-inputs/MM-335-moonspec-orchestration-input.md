# MM-335 MoonSpec Orchestration Input

## Source

- Jira issue: MM-335
- Board scope: MM
- Issue type: Story
- Current status at fetch time: Backlog
- Summary: [MM-318] Enforce auth security boundaries for workloads and browser surfaces
- Canonical source: Synthesized from the trusted `jira.search_issues` MCP response because the response did not expose `recommendedImports.presetInstructions`; `jira.get_issue` was policy-denied for project `MM`.

## Canonical MoonSpec Feature Request

MM-335: [MM-318] Enforce auth security boundaries for workloads and browser surfaces

User Story
As a security reviewer, I can verify that OAuth credentials never leak into workflow history, browser responses, logs, artifacts, raw volume listings, or Docker-backed workload containers unless a workload credential mount is explicitly declared and justified.

Source Document
- Path: docs/ManagedAgents/OAuthTerminal.md
- Sections: 4. Volume Targeting Rules, 8. Verification, 9. Security Model, 11. Required Boundaries
- Coverage IDs: DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-022
- Breakdown Story ID: STORY-004
- Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthtermina-74125184/stories.json

## Supplemental Acceptance Criteria

- Workflow payloads carry profile_id, volume_ref, and mount target refs only, never credential file contents.
- Logs, diagnostics, artifacts, and browser responses redact or omit token values, credential files, environment dumps, and raw auth-volume listings.
- OAuth management actions require provider-profile management permission at every control surface.
- Workload containers launched from a managed session do not inherit auth volumes by default.
- Any workload credential mount requires an explicit workload profile declaration and justification.
- Tests cover the real adapter or service boundary where OAuth terminal, Provider Profile, managed-session controller, Codex runtime, and workload orchestration exchange metadata.

Requirements
- Apply secret redaction and omission rules to all OAuth/profile/session status surfaces.
- Enforce authorization for provider-profile and OAuth session mutation operations.
- Fail closed when a workload container would implicitly inherit a managed-runtime auth volume.
- Keep ownership boundaries explicit between enrollment, profile metadata, session mounts, runtime seeding, and workload orchestration.

Independent Test
Execute boundary tests that inject secret-like fixture credential files and environment values, exercise OAuth status APIs, profile APIs, managed-session launch metadata, artifact/log publishing, and workload container launch, then assert secret values and raw volume listings never appear and undeclared workload auth mounts are rejected.

Notes
- Short name: auth-security-boundaries
- Dependencies: STORY-001, STORY-002, STORY-003
- Needs clarification: None

Out Of Scope
- Implementing the OAuth terminal UI itself
- Implementing Codex App Server protocol behavior
- Declaring new credential-requiring workload profiles

Source Design Coverage
- DESIGN-REQ-008: Owns no implicit workload auth inheritance.
- DESIGN-REQ-009: Owns credential leakage prevention across history, logs, artifacts, and UI.
- DESIGN-REQ-017: Owns no-secret verification evidence at both boundaries.
- DESIGN-REQ-018: Owns authorization enforcement for OAuth/profile management.
- DESIGN-REQ-019: Owns browser response safety.
- DESIGN-REQ-021: Owns cross-component boundary enforcement tests.
- DESIGN-REQ-022: Owns non-goal enforcement around auth inheritance and generic shell exposure.
