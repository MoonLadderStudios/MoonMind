# Feature Specification: Claude Manual Auth API

**Feature Branch**: `236-claude-manual-auth-api`
**Created**: 2026-04-22
**Status**: Draft
**Input**:

```text
Jira issue: MM-447 from MM project
Summary: Add secret-safe Claude manual auth API and service behavior
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-447 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-447: Add secret-safe Claude manual auth API and service behavior

Source Reference
Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
Source Sections:
- 3. Design decision
- 4. Desired profile shape
- 6.1 Do not reuse /api/v1/oauth-sessions as-is
- 6.2 Add a separate manual-auth path
- 7. Secrets handling
- 10.2 Backend
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-004
- DESIGN-REQ-006
- DESIGN-REQ-010
- DESIGN-REQ-012
- DESIGN-REQ-014

User Story
As Mission Control, I can submit a Claude Anthropic token to a dedicated manual-auth backend path that validates it, stores it as a Managed Secret, binds claude_anthropic to that secret, syncs provider profile state, and returns only secret-free readiness metadata.

Acceptance Criteria
- The Claude manual auth path does not require volume_ref, volume_mount_path, mounted Docker files, oauth_volume, or oauth_home finalization.
- Successful commit stores the token only in Managed Secrets and stores only secret references in the provider profile row.
- The returned response contains readiness, validation timestamp/status, secret existence, and profile readiness without returning the token.
- Invalid tokens, failed upstream validation, and unauthorized callers fail without leaking submitted token material.
- Tests prove no raw token appears in profile rows, workflow-shaped payloads, route responses, logs captured by the test, or validation failure messages.

Requirements
- Dedicated backend behavior must be separate from the existing volume-first oauth-sessions finalize path.
- The resulting profile must target credential_source=secret_ref and runtime_materialization_mode=api_key_env.
- Secret refs should include anthropic_api_key bound to a managed secret such as db://claude_anthropic_token.
- clear_env_keys must remove conflicting Anthropic/OpenAI keys before launch materialization.

Implementation Notes
- Preserve MM-447 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Scope the implementation to the backend manual-auth commit behavior for Claude Anthropic provider profiles.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for the provider-profile-backed manual token enrollment flow.
- Keep this path separate from `/api/v1/oauth-sessions` and its volume-first OAuth terminal finalization semantics.
- Store submitted token material only in Managed Secrets; provider profiles, workflow-shaped payloads, route responses, validation failures, notices, logs, and artifacts must remain secret-free.
- Bind `claude_anthropic` through a `secret_ref` profile shape that launches with `api_key_env` materialization and clears conflicting Anthropic/OpenAI environment variables before runtime launch.
- Sync provider profile state after a successful commit so runtime-visible provider profile data reflects the new secret reference and readiness metadata.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-447 is blocked by MM-448, whose embedded status is Selected for Development.
- Trusted Jira link metadata also shows MM-447 blocks MM-446, which is not a blocker for MM-447 and is ignored for dependency gating.

Needs Clarification
- None
```

## Classification

Single-story runtime feature request. The brief contains one independently testable backend behavior change: Mission Control can submit a Claude Anthropic token to a dedicated manual-auth provider-profile path that validates the token, stores it only in Managed Secrets, updates the `claude_anthropic` profile to a secret-reference launch shape, syncs provider profile state, and returns only secret-free readiness metadata.

## User Story - Secret-Safe Claude Manual Auth Commit

**Summary**: As Mission Control, I want a dedicated Claude manual-auth backend commit path so that Claude Anthropic token enrollment updates provider profile readiness without exposing submitted token material.

**Goal**: Mission Control can complete the backend side of Claude Anthropic manual token enrollment through a non-volume auth path that stores raw token material only in Managed Secrets and exposes only secret references and readiness metadata to callers and runtime profile projections.

**Independent Test**: Submit a Claude Anthropic token to the manual-auth commit path for a supported `claude_code` Anthropic provider profile, then verify that the token is validated, stored only in Managed Secrets, the profile is updated to `secret_ref` and `api_key_env`, the provider profile manager is synced with secret-reference metadata, launch materialization can resolve the resulting `db://` secret reference, and route responses or failure messages never contain the submitted token.

**Acceptance Scenarios**:

1. **Given** a supported Claude Anthropic provider profile still uses a volume-backed shape, **When** Mission Control submits a valid returned token to the manual-auth commit path, **Then** the system validates the token, stores it as a Managed Secret, updates the profile to secret-reference materialization, syncs provider profile state, and returns a secret-free ready response.
2. **Given** a successful manual-auth commit, **When** the provider profile is fetched or projected to workflow/runtime consumers, **Then** it contains only secret references, readiness metadata, and launch-safe profile fields, never the submitted token.
3. **Given** the updated provider profile is used for runtime launch materialization, **When** the materializer resolves profile secrets, **Then** the `db://` secret reference can be resolved and injected through the configured Anthropic environment binding after conflicting environment keys are cleared.
4. **Given** an invalid token, an upstream validation failure, or an unauthorized caller, **When** the manual-auth commit is attempted, **Then** the request fails without creating or updating a successful provider binding and without echoing submitted token material.
5. **Given** the existing `/api/v1/oauth-sessions` terminal flow remains available for Codex-style auth, **When** Claude manual token auth is committed, **Then** it does not require auth volumes, mounted Docker files, `oauth_volume`, or `oauth_home` finalization.

### Edge Cases

- The submitted token is blank, malformed, or below the accepted token shape.
- Upstream validation rejects the token or cannot be reached.
- The target provider profile does not exist, is not visible to the caller, or is not a Claude Code Anthropic profile.
- The target profile has existing volume-backed fields that must be removed from the launch shape.
- A previously stored Claude token is replaced by a newly validated token.
- Secret references must be resolvable by slug-style `db://` references rather than only legacy UUID payloads.

## Assumptions

- A collapsed commit endpoint is sufficient for this story; separate start, validate, and delete endpoints remain out of scope unless future stories require them.
- Secret ownership follows the existing Managed Secrets and provider profile ownership boundaries.
- The runtime-visible secret reference may use the repository's currently valid `db://` slug format even when Jira examples use an illustrative slug spelling.

## Source Design Requirements

- **DESIGN-REQ-002** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3): Claude Anthropic Settings auth MUST use a provider-profile-backed manual token flow rather than forcing paste-back token auth through volume-first OAuth session finalization. Scope: in scope, mapped to FR-001, FR-002, FR-003, FR-010.
- **DESIGN-REQ-004** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 4): The resulting `claude_anthropic` profile SHOULD target `credential_source=secret_ref`, `runtime_materialization_mode=api_key_env`, a Managed Secret binding, conflict-clearing environment keys, and an Anthropic API key environment template. Scope: in scope, mapped to FR-004, FR-005, FR-006, FR-007.
- **DESIGN-REQ-006** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 6.1): The manual token flow MUST NOT reuse `/api/v1/oauth-sessions` semantics that require Docker auth volumes, mounted files, or `oauth_home` finalization. Scope: in scope, mapped to FR-001, FR-003, FR-010.
- **DESIGN-REQ-010** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 6.2): The backend manual-auth path MUST validate caller permission, validate token input, perform safe upstream validation, write or update the managed secret, update the provider profile, sync provider profile state, and return a secret-free readiness summary. Scope: in scope, mapped to FR-001 through FR-009.
- **DESIGN-REQ-012** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 7): Submitted token material MUST never be stored in provider profiles, returned to the browser, placed in workflow payloads, or exposed through logs, notices, validation failures, or artifacts. Scope: in scope, mapped to FR-008, FR-009, FR-011, FR-012.
- **DESIGN-REQ-014** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 10.2): Backend behavior MUST validate tokens, write managed secrets, update provider profiles, sync `ProviderProfileManager`, and return secret-free readiness metadata. Scope: in scope, mapped to FR-001 through FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a dedicated Claude manual-auth commit path for supported Claude Code Anthropic provider profiles.
- **FR-002**: The commit path MUST enforce existing provider profile management authorization before mutating secrets or profiles.
- **FR-003**: The commit path MUST reject unsupported provider profiles without falling back to the volume-first OAuth session flow.
- **FR-004**: A successful commit MUST validate the submitted token format and perform a safe upstream validation probe before storing it as ready.
- **FR-005**: A successful commit MUST store raw token material only in Managed Secrets.
- **FR-006**: A successful commit MUST update the provider profile to `credential_source=secret_ref` and `runtime_materialization_mode=api_key_env`.
- **FR-007**: A successful commit MUST remove volume-backed launch fields and configure a secret-reference Anthropic launch binding with conflicting Anthropic/OpenAI environment keys cleared before launch materialization.
- **FR-008**: A successful commit MUST sync provider profile state so runtime-visible profile projections contain the new secret-reference launch shape and readiness metadata.
- **FR-009**: A successful commit response MUST include secret-free readiness metadata, including connected state, validation timestamp or status, backing secret existence, profile launch readiness, and profile identity.
- **FR-010**: The manual-auth path MUST NOT require `volume_ref`, `volume_mount_path`, mounted Docker files, `oauth_volume`, or `oauth_home` finalization.
- **FR-011**: Invalid tokens, failed upstream validation, and unauthorized callers MUST fail without leaking submitted token material in responses or validation messages.
- **FR-012**: Provider profile rows, workflow-shaped payloads, route responses, logs captured by tests, and validation failure messages MUST NOT include raw submitted token material.
- **FR-013**: Runtime secret resolution MUST support the secret-reference form produced by the manual-auth commit path so launch materialization can inject the stored Anthropic credential through the profile binding.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-447 and the source design mappings.

### Key Entities

- **Claude Manual Auth Commit Request**: A secret-bearing request containing a returned Claude token and optional operator-facing account label.
- **Managed Claude Token Secret**: The encrypted managed secret entry that stores the submitted token and metadata about its provider profile binding.
- **Claude Anthropic Provider Profile Binding**: The provider profile state that references the managed secret and describes the launch materialization shape.
- **Claude Manual Auth Readiness Metadata**: Secret-free status data returned to Mission Control and projected through provider profile state.
- **Provider Profile Manager Sync Payload**: Runtime-visible profile projection that must carry only secret references and readiness metadata, not token material.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Route-level tests confirm a successful commit stores the token only in Managed Secrets and the response body contains zero occurrences of the submitted token.
- **SC-002**: Route-level tests confirm the fetched provider profile after commit contains `secret_ref`, `api_key_env`, secret references, cleared conflicting environment keys, readiness metadata, and zero occurrences of the submitted token.
- **SC-003**: Failure-path tests confirm malformed or invalid token submissions do not call successful persistence and return a secret-free validation failure.
- **SC-004**: Authorization or unsupported-profile checks prevent non-owners or non-Claude Anthropic profiles from mutating managed secrets or provider profile launch bindings.
- **SC-005**: Runtime materialization tests confirm the produced `db://` secret-reference binding can be resolved for launch injection.
- **SC-006**: Regression tests confirm Codex-style OAuth session behavior is not required or invoked for Claude manual token commit.
- **SC-007**: Traceability verification confirms MM-447 and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-012, and DESIGN-REQ-014 remain present in MoonSpec artifacts and final verification evidence.
