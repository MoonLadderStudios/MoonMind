# Feature Specification: Auth Security Boundaries

**Feature Branch**: `177-auth-security-boundaries`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**: MM-335: [MM-318] Enforce auth security boundaries for workloads and browser surfaces

## Original Jira Preset Brief

```text
MM-335: [MM-318] Enforce auth security boundaries for workloads and browser surfaces

User Story
As a security reviewer, I can verify that OAuth credentials never leak into workflow history, browser responses, logs, artifacts, raw volume listings, or Docker-backed workload containers unless a workload credential mount is explicitly declared and justified.

Source Document
- Path: docs/ManagedAgents/OAuthTerminal.md
- Sections: 4. Volume Targeting Rules, 8. Verification, 9. Security Model, 11. Required Boundaries
- Coverage IDs: DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-022
- Breakdown Story ID: STORY-004
- Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthtermina-74125184/stories.json

Acceptance Criteria
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
```

## User Story - Verify OAuth Credential Security Boundaries

**Summary**: As a security reviewer, I want OAuth credential boundaries to be enforced across workflows, browser surfaces, logs, artifacts, and workload launches so that durable provider credentials cannot leak or be inherited accidentally.

**Goal**: Provide independently verifiable evidence that OAuth credential refs remain compact and sanitized, privileged OAuth/profile operations require the right permission, and Docker-backed workloads do not receive managed-runtime auth volumes unless explicitly declared and justified.

**Independent Test**: Execute boundary tests with secret-like fixture credential files and environment values across OAuth status APIs, provider profile APIs, managed-session launch metadata, artifact/log publication, and workload launch; the story passes only when no credential values or raw auth-volume listings appear and undeclared workload auth mounts are rejected.

**Acceptance Scenarios**:

1. **Given** an OAuth-backed provider profile and managed Codex session metadata, **when** workflow payloads, session summaries, diagnostics, logs, artifacts, and browser responses are inspected, **then** they contain only compact refs such as profile IDs, volume refs, and mount target refs and never contain credential file contents, token values, environment dumps, or raw auth-volume listings.
2. **Given** an operator without provider-profile management permission, **when** they attempt to create, attach to, cancel, finalize, select, or mutate OAuth/profile credential surfaces, **then** MoonMind rejects the action without exposing credential metadata beyond sanitized failure context.
3. **Given** a managed session launches a Docker-backed workload without an explicit workload credential declaration, **when** workload mounts and launch metadata are evaluated, **then** the workload receives only declared workspace/cache mounts and does not inherit any managed-runtime auth volume.
4. **Given** a workload profile explicitly requests a credential mount with justification, **when** the workload is launched, **then** MoonMind records the explicit declaration and justification while still preventing credential contents from entering workflow history, logs, artifacts, or browser responses.

### Edge Cases

- A credential file contains values that resemble API keys, bearer tokens, cookies, private keys, or provider session JSON.
- A diagnostic or artifact publisher receives nested structures that include credential file paths, raw directory listings, environment variables, or token-like values.
- A provider-profile or OAuth mutation is attempted through a secondary control surface rather than the primary browser flow.
- A workload launch request includes a mount that points at the managed-runtime auth volume but lacks an explicit workload credential declaration.
- A workload credential declaration is present but lacks a human-readable justification.

## Assumptions

- Existing STORY-001, STORY-002, and STORY-003 work provides OAuth profile metadata, OAuth terminal session lifecycle, and managed Codex auth materialization boundaries that this story can harden and verify.
- Explicit workload credential declarations are represented in workload launch/profile metadata rather than as ad hoc raw Docker mount strings supplied directly by a browser caller.

## Source Design Requirements

- **DESIGN-REQ-008**: Workload containers launched from managed sessions must not inherit auth volumes by default. Source: `docs/ManagedAgents/OAuthTerminal.md` section 4. Scope: in scope. Maps to FR-006 and FR-007.
- **DESIGN-REQ-009**: Workflow history, logs, artifacts, and browser-visible surfaces must not expose credential file contents, token values, environment dumps, or raw auth-volume listings. Source: sections 4 and 9. Scope: in scope. Maps to FR-001, FR-002, FR-003, and FR-004.
- **DESIGN-REQ-017**: Verification must happen at OAuth/profile and managed-session launch boundaries without copying credential contents into workflow payloads, artifacts, logs, or UI responses. Source: section 8. Scope: in scope. Maps to FR-008 and FR-009.
- **DESIGN-REQ-018**: OAuth session and provider-profile management actions require provider-profile management permission. Source: section 9. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-019**: Browser responses may expose status, timestamps, failure reasons, and registered profile summaries, but not credential files, token values, environment dumps, or raw auth-volume listings. Source: section 9. Scope: in scope. Maps to FR-003 and FR-004.
- **DESIGN-REQ-021**: Boundary ownership between OAuth terminal, Provider Profile, managed-session controller, Codex runtime, and Docker workload orchestration must remain explicit and testable. Source: section 11. Scope: in scope. Maps to FR-010.
- **DESIGN-REQ-022**: OAuth terminal code must not become a generic shell or workload credential propagation mechanism. Source: sections 10 and 11. Scope: in scope. Maps to FR-006, FR-007, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Workflow payloads and durable run metadata that reference OAuth-backed provider credentials MUST carry compact identifiers and mount target refs only, never credential file contents or raw credential directory listings.
- **FR-002**: Log and diagnostic publication MUST redact or omit token-like values, credential file contents, raw auth-volume listings, and environment dumps before they become persisted or browser-visible.
- **FR-003**: OAuth session, provider profile, and managed-session browser responses MUST expose only sanitized status, timestamps, failure reasons, and profile summaries.
- **FR-004**: Browser responses MUST NOT expose credential files, token values, environment dumps, raw auth-volume listings, or secret-like values derived from OAuth/profile/session internals.
- **FR-005**: OAuth session creation, terminal attachment, cancellation, finalization, provider-profile selection, and provider-profile mutation MUST require provider-profile management permission at each control surface.
- **FR-006**: Docker-backed workload containers launched from a managed session MUST NOT inherit managed-runtime auth volumes by default.
- **FR-007**: Any workload credential mount MUST require an explicit workload profile declaration and a non-empty justification before launch.
- **FR-008**: Boundary verification MUST include fixture credential files and secret-like environment values at OAuth/profile and managed-session launch boundaries without persisting or returning those secret values.
- **FR-009**: Verification evidence MUST prove that workflow payloads, logs, artifacts, diagnostics, and browser responses contain no raw credential contents or raw auth-volume listings.
- **FR-010**: Ownership boundaries between OAuth terminal enrollment, provider profile metadata, managed-session container mounts, Codex runtime materialization, and Docker workload orchestration MUST remain explicit in observable launch and verification behavior.

### Key Entities

- **Credential Ref**: Compact metadata such as provider profile ID, volume ref, and explicit mount target used to identify credential material without carrying credential contents.
- **Sanitized Surface**: Any workflow payload, browser response, log, diagnostic record, artifact, or session summary that can be persisted or inspected by operators.
- **Workload Credential Declaration**: Workload profile metadata that explicitly requests a credential mount and records the justification for allowing that mount.
- **Boundary Verification Evidence**: Test-visible proof that each credential boundary uses refs, authorization checks, redaction, omission, or fail-closed behavior as required.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Boundary tests using secret-like fixture credentials find zero credential contents, token values, raw auth-volume listings, or environment dumps in workflow payloads, browser responses, logs, diagnostics, artifacts, and session summaries.
- **SC-002**: Authorization tests prove every OAuth/profile management action listed in FR-005 rejects a caller without provider-profile management permission.
- **SC-003**: Workload launch tests prove undeclared managed-runtime auth-volume inheritance fails closed.
- **SC-004**: Workload launch tests prove explicitly declared credential mounts require a non-empty justification and remain sanitized in launch metadata and evidence.
- **SC-005**: Source design coverage for DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, and DESIGN-REQ-022 is mapped to passing verification evidence.
