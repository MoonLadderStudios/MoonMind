# Feature Specification: Claude OAuth Authorization and Redaction Guardrails

**Feature Branch**: `245-claude-oauth-guardrails`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-482 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `spec.md` (Input).
Classification: single-story runtime feature request.

## Original Preset Brief

```text
# MM-482 MoonSpec Orchestration Input

## Source

- Jira issue: MM-482
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Authorization and Redaction Guardrails
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-482 from MM project
Summary: Claude OAuth Authorization and Redaction Guardrails
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-482 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-482: Claude OAuth Authorization and Redaction Guardrails

Source Reference
- Source Document: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source Title: Claude Anthropic OAuth in Settings

Source Sections
- 2. OAuth Profile Shape
- 3.4 Claude Sign-In Ceremony
- 5. Verification
- 7. Runtime Launch Behavior
- 9. Security Requirements

Coverage IDs
- DESIGN-REQ-004
- DESIGN-REQ-009
- DESIGN-REQ-013
- DESIGN-REQ-016
- DESIGN-REQ-017
- DESIGN-REQ-018

User Story
As an operator and platform maintainer, I can rely on Claude OAuth lifecycle operations, terminal output, errors, logs, artifacts, and profile rows to enforce authorization and redact secret-like values across the full flow.

Acceptance Criteria
- Given an unauthenticated or unauthorized operator attempts to start, attach to, cancel, finalize, or repair a Claude OAuth session, then MoonMind denies the operation.
- Given a browser terminal attach token is reused or expired, then attach fails.
- Given terminal output, failure reasons, logs, or artifacts contain secret-like values, then externally visible output is redacted.
- Given provider profile rows are read, then they contain refs and metadata only, never credential file contents.
- Given OAuth auth volume metadata is surfaced, then the volume is described as a credential store and not exposed as a task workspace or audit artifact.
- Given guardrail tests run, then they cover the real API/workflow/activity or adapter boundary rather than only isolated helpers.

Requirements
- Enforce authorization across Claude OAuth start, attach, cancel, finalize, and repair operations.
- Make browser terminal attach tokens short-lived and single-use.
- Redact secret-like values from terminal output, failure reasons, logs, and artifacts.
- Keep provider profile rows limited to refs and metadata.
- Treat OAuth auth volumes strictly as credential stores.

Implementation Notes
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the governing design source for profile shape, sign-in ceremony, verification, runtime launch behavior, and security requirements.
- Keep authorization enforcement consistent across Claude OAuth start, attach, cancel, finalize, and repair operations.
- Ensure attach tokens are both short-lived and single-use.
- Redact secret-like values from terminal output, failure reasons, logs, and artifacts before they become externally visible.
- Preserve provider profile rows as refs-and-metadata only; never expose credential file contents.
- Surface OAuth auth volumes strictly as credential stores, not as task workspaces or audit artifacts.
- Add or update guardrail tests at the real API, workflow, activity, or adapter boundary rather than only isolated helper coverage.

Needs Clarification
- None
```

## User Story - Claude OAuth Authorization and Redaction Guardrails

**Summary**: As an operator and platform maintainer, I want Claude OAuth lifecycle operations and observable outputs to enforce authorization and redact secret-like values so credential enrollment and runtime use remain secure across the full flow.

**Goal**: Claude OAuth start, attach, cancel, finalize, repair, runtime launch, logs, artifacts, and provider-profile surfaces consistently reject unauthorized access, treat auth volumes as credential stores, and expose only secret-free metadata.

**Independent Test**: Exercise or simulate authorized and unauthorized Claude OAuth lifecycle operations, terminal attach-token reuse, terminal/log/artifact output containing secret-like values, and provider-profile read paths, then verify unauthorized actions fail closed, attach tokens are single-use and short-lived, auth volumes are not exposed as workspaces or artifacts, and all externally visible outputs remain secret-free.

**Acceptance Scenarios**:

1. **Given** an operator lacks permission for a Claude OAuth session, **When** they attempt to start, attach to, cancel, finalize, or repair it, **Then** the system rejects the operation before terminal access, verification, profile mutation, or other side effects occur.
2. **Given** a browser terminal attach token is expired, reused, or presented by the wrong operator, **When** terminal attachment is attempted, **Then** the attachment is denied and the token cannot be used to access the Claude OAuth terminal.
3. **Given** terminal output, failure summaries, diagnostics, logs, or artifacts include secret-like values, **When** the system records or exposes those surfaces, **Then** only redacted or secret-free output is visible to operators and downstream systems.
4. **Given** a provider profile row or OAuth verification result is returned, **When** it is inspected through API or UI surfaces, **Then** it contains refs and metadata only and never credential file contents, raw tokens, or other secret-bearing payloads.
5. **Given** the Claude OAuth auth volume or mounted Claude home is referenced during verification, launch, or auditing, **When** external metadata is produced, **Then** the volume is described only as a credential store and is not treated as a task workspace or artifact-backed path.
6. **Given** guardrail validation runs for Claude OAuth, **When** implementation evidence is collected, **Then** coverage includes the real API, workflow, activity, terminal-bridge, or adapter boundary rather than only isolated helper tests.

### Edge Cases

- An operator owns one Claude OAuth session but attempts to attach to or repair another operator's session.
- A terminal attach token is still within session lifetime but has already been consumed once.
- Redaction logic encounters secret-like data in structured JSON, multiline terminal text, or nested error payloads.
- Existing successful OAuth verification or runtime-launch behavior must remain intact while authorization and redaction controls are strengthened.
- Debug or diagnostic output attempts to include raw auth-volume paths, credential filenames, or provider-profile payloads.

## Assumptions

- MM-478, MM-479, MM-480, and MM-481 cover Claude OAuth session creation, browser sign-in, verification/profile registration, and runtime launch behavior respectively; MM-482 is the cross-cutting guardrail story spanning those lifecycle surfaces.
- `docs/ManagedAgents/ClaudeAnthropicOAuth.md` is the authoritative source design for Claude OAuth profile shape, sign-in ceremony, verification, runtime launch behavior, and security requirements.
- Runtime intent is required; documentation updates alone are not sufficient for this story.

## Source Design Requirements

- **DESIGN-REQ-004** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 2): OAuth-backed Claude profile and related metadata surfaces must preserve the documented credential-store boundaries and must not degrade into exposing credential-bearing content. Scope: in scope, mapped to FR-004 and FR-005.
- **DESIGN-REQ-009** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.4): Claude OAuth terminal interaction must remain gated by operator-waiting and attach-token controls so only authorized attachment succeeds. Scope: in scope, mapped to FR-002 and FR-003.
- **DESIGN-REQ-013** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 5): OAuth verification and related observable outputs must expose only secret-free metadata and never raw credential contents. Scope: in scope, mapped to FR-004 and FR-006.
- **DESIGN-REQ-016** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 9): Only authorized operators may start, attach to, cancel, finalize, or repair Claude OAuth sessions. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-017** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 9): Browser terminal attach tokens must be short-lived and single-use. Scope: in scope, mapped to FR-002 and FR-003.
- **DESIGN-REQ-018** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` sections 7 and 9): Claude OAuth runtime launch and surrounding surfaces must keep auth-volume material treated as credential storage, avoid raw credential contents in workflow history/logs/artifacts, and maintain secure runtime boundaries. Scope: in scope, mapped to FR-005, FR-006, and FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST enforce authorization checks across Claude OAuth start, attach, cancel, finalize, and repair operations before any side effects occur.
- **FR-002**: System MUST restrict browser terminal attachment for Claude OAuth sessions to the owning authorized operator.
- **FR-003**: System MUST require Claude OAuth browser terminal attach tokens to be short-lived, single-use, and rejected after expiration or prior consumption.
- **FR-004**: System MUST redact or exclude secret-like values from terminal output, failure summaries, logs, diagnostics, artifacts, API responses, and other externally visible Claude OAuth surfaces.
- **FR-005**: System MUST ensure provider-profile rows and related OAuth metadata surfaces expose refs and metadata only and never credential file contents or raw token values.
- **FR-006**: System MUST treat Claude OAuth auth volumes and mounted Claude-home paths strictly as credential stores rather than task workspaces, artifact-backed paths, or audit artifact content.
- **FR-007**: System MUST provide guardrail validation at the real API, workflow, activity, terminal-bridge, or adapter boundary for the authorization, token, redaction, and credential-store protections in this story.
- **FR-008**: System MUST preserve MM-482 in implementation notes, verification output, commit text, and pull request metadata for traceability.

### Key Entities

- **Claude OAuth Session Authorization Boundary**: The access-control decision surface governing who may start, attach to, cancel, finalize, or repair a Claude OAuth session.
- **OAuth Terminal Attach Token**: A short-lived, single-use session attachment credential scoped to the owning operator and Claude OAuth terminal bridge.
- **Secret-Free Observable Output**: Any operator-visible terminal output, failure summary, diagnostic record, artifact, API response, or provider-profile payload with secret-like values removed or excluded.
- **Claude OAuth Credential Store Surface**: The auth volume, mounted Claude-home path, and related metadata that must remain credential-storage only rather than becoming a workspace or artifact content source.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Boundary tests prove unauthorized Claude OAuth start, attach, cancel, finalize, and repair attempts are rejected before side effects occur.
- **SC-002**: Terminal attach tests prove expired or reused Claude OAuth attach tokens are rejected and cannot access the terminal bridge.
- **SC-003**: Redaction tests prove secret-like values are absent from terminal output, failure summaries, logs, diagnostics, artifacts, and API-visible metadata after Claude OAuth flows run.
- **SC-004**: Provider-profile or verification-surface tests prove only refs and metadata are returned and no credential file contents or raw token values are exposed.
- **SC-005**: Runtime-launch or audit-surface tests prove Claude OAuth auth volumes are not treated as task workspaces or artifact-backed content paths.
- **SC-006**: Verification evidence covers the real API, workflow, activity, terminal-bridge, or adapter boundary for each protection in scope rather than only isolated helper functions.
