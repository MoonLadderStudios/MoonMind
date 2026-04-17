# Feature Specification: Managed GitHub Secret Materialization

**Feature Branch**: `201-managed-github-secret-materialization`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-320 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-320-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-320 from MM project
Summary: Better gh secret handling
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-320 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-320: Better gh secret handling

Issue Brief
- Preset issue type from Jira description: Task
- Preset summary from Jira description: Align managed-session GitHub auth with Secrets System launch materialization model

Description
MoonMind currently supports managed-session GitHub clone auth by resolving `GITHUB_TOKEN` before `agent_runtime.launch_session` and materializing it into host-side git subprocesses through an environment-scoped credential helper.

This fixes the immediate clone failure, but the long-term implementation should align more directly with `docs/Security/SecretsSystem.md`: durable contracts should carry secret references or materialization descriptors, not raw secret values, and runtime launch should produce auditable, scoped materialization events.

Objective
Refactor managed-session GitHub authentication so Codex session launch receives a reference-based GitHub credential contract and resolves/materializes the credential only at the controlled runtime launch boundary.

Acceptance Criteria
- Managed-session clone of a private GitHub repo succeeds without prior `gh auth setup-git` for the worker Unix user.
- Temporal workflow payloads and histories contain no raw GitHub token values.
- Launch request durable data contains only a secret reference or non-sensitive materialization descriptor.
- Missing or revoked GitHub credential fails before clone with a clear, actionable, redaction-safe error.
- Git clone/fetch/push failure messages redact secret-like values.
- Audit/diagnostic metadata records that GitHub credential materialization was required for the run without exposing the credential.
- Existing local-first managed secret behavior continues to work for `GITHUB_TOKEN` and `GITHUB_PAT`.
- Unit tests cover the activity/controller boundary and redaction.
- At least one workflow/activity boundary test covers the real managed-session launch invocation shape.

## Classification

Single-story runtime feature request. The request targets one independently testable behavior: managed-session GitHub credentials are represented durably as references or materialization descriptors and resolved only at the runtime launch boundary.

## User Story - Managed GitHub Secret Materialization

**Summary**: As a task operator, I can launch managed Codex sessions against private GitHub repositories while MoonMind keeps durable launch contracts reference-based and materializes the GitHub credential only for scoped host-side git operations.

**Goal**: Private repository clone, fetch, and push preparation works without worker-local `gh auth setup-git`, while workflow payloads, launch request durable data, container environment, artifacts, logs, and diagnostics do not expose raw GitHub token values.

**Independent Test**: Can be fully tested by launching or simulating a managed Codex session with a private GitHub repository workspace spec and a reference-backed GitHub credential, then verifying host git commands receive a launch-scoped credential helper, the managed session container does not receive the raw token, and all failure details are redacted.

**Acceptance Scenarios**:

1. **Given** a managed Codex launch request carries a GitHub credential reference descriptor, **When** the controller prepares a private GitHub workspace, **Then** host-side git clone/fetch/push setup receives a scoped credential helper and succeeds without relying on persistent worker `gh` setup.
2. **Given** the launch request is serialized in workflow/activity payloads or sent into the managed session container, **When** the payload is inspected, **Then** it contains only a secret reference or non-sensitive materialization descriptor and never the raw GitHub token.
3. **Given** a GitHub credential is missing, revoked, or unresolvable, **When** workspace preparation needs GitHub auth, **Then** launch fails before clone with a clear, actionable, redaction-safe error.
4. **Given** git clone, fetch, or push preparation fails and provider output includes secret-like text, **When** MoonMind reports the failure, **Then** the raw credential and secret-like value are redacted.
5. **Given** an operator has configured local-first managed secrets under `GITHUB_TOKEN` or `GITHUB_PAT`, **When** a managed Codex launch needs GitHub auth, **Then** MoonMind can derive a non-sensitive launch descriptor and resolve the credential at the controlled runtime launch boundary.
6. **Given** GitHub credential materialization was used for a launch, **When** diagnostics or metadata are emitted, **Then** they record that materialization was required without exposing the value.

### Edge Cases

- The launch request includes a legacy raw `GITHUB_TOKEN` in its environment.
- Both `GITHUB_TOKEN` and `GITHUB_PAT` managed secrets are configured.
- `GITHUB_TOKEN_SECRET_REF` or `WORKFLOW_GITHUB_TOKEN_SECRET_REF` is configured but cannot be resolved.
- The workspace repository is public or local and does not require GitHub auth.
- Git failure output embeds the token in stderr, stdout, or a rendered command.
- Existing managed profile materialization also contributes environment values.

## Assumptions

- Runtime mode is required; documentation-only changes do not satisfy MM-320.
- The current environment-scoped git credential helper may remain the final host subprocess materialization mechanism if it is driven by a launch-scoped resolved secret value.
- Existing local-first managed secret slugs `GITHUB_TOKEN` and `GITHUB_PAT` remain the supported fallback source.
- The username-free repo input model remains owner/repo, URL, or local path.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Security/SecretsSystem.md` sections 1, 4.1, 5.1; MM-320 brief): Durable system contracts MUST store GitHub credential references or materialization descriptors rather than raw token values. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-002** (Source: `docs/Security/SecretsSystem.md` sections 8.1, 8.2; MM-320 brief): GitHub credential values MUST resolve only at controlled execution boundaries and MUST NOT be written back into workflow payloads, task definitions, run metadata, or artifacts. Scope: in scope. Maps to FR-004, FR-005, FR-006.
- **DESIGN-REQ-003** (Source: `docs/Security/SecretsSystem.md` sections 9.2, 9.3; MM-320 brief): Runtime materialization MUST be launch-scoped and as narrow as feasible for third-party runtime or subprocess needs. Scope: in scope. Maps to FR-007, FR-008.
- **DESIGN-REQ-004** (Source: `docs/Security/SecretsSystem.md` sections 4.6, 10; MM-320 brief): Missing, revoked, or unsupported secrets MUST fail fast with operator-visible diagnostics that do not expose raw values. Scope: in scope. Maps to FR-009, FR-010.
- **DESIGN-REQ-005** (Source: MM-320 brief): Existing local-first managed secret behavior for `GITHUB_TOKEN` and `GITHUB_PAT` MUST continue to support private repository clone and push. Scope: in scope. Maps to FR-011.
- **DESIGN-REQ-006** (Source: MM-320 brief): Managed-session GitHub auth MUST keep the existing username-free repository input model. Scope: in scope. Maps to FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a non-sensitive managed-session GitHub credential descriptor that can identify whether launch-time GitHub materialization is required.
- **FR-002**: System MUST support a GitHub credential descriptor backed by an explicit secret reference.
- **FR-003**: System MUST support a GitHub credential descriptor backed by the local-first managed secret fallback without embedding the resolved token in durable request data.
- **FR-004**: System MUST resolve the GitHub credential immediately before host-side git workspace preparation.
- **FR-005**: System MUST NOT pass raw GitHub token values in workflow/activity launch request payloads.
- **FR-006**: System MUST NOT pass raw GitHub token values into the managed session container environment or container launch payload.
- **FR-007**: System MUST materialize GitHub credentials only for host-side git subprocesses that need them.
- **FR-008**: System MUST set git non-interactive behavior for credential-backed host git commands.
- **FR-009**: System MUST fail before clone/fetch/push when a required GitHub credential descriptor cannot be resolved.
- **FR-010**: System MUST redact secret-like values from git and launch failure messages.
- **FR-011**: System MUST preserve support for local-first `GITHUB_TOKEN` and `GITHUB_PAT` managed secrets.
- **FR-012**: System MUST preserve username-free repository input: owner/repo, URL, or local path only.
- **FR-013**: System MUST expose redaction-safe diagnostics or metadata indicating GitHub credential materialization was required for launch.
- **FR-014**: Automated coverage MUST include the activity/controller boundary and one workflow/activity invocation shape for launch credential descriptors.

### Key Entities

- **Managed GitHub Credential Descriptor**: Non-sensitive launch contract data identifying the credential source kind and optional secret reference needed for GitHub host git operations.
- **GitHub Credential Materialization**: Launch-scoped in-memory resolved token plus git credential helper environment used only for host-side git subprocesses.
- **Managed Session Launch Request**: Durable launch boundary payload for a Codex managed session, now carrying non-sensitive credential materialization data rather than raw GitHub tokens.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove launch request serialization with a GitHub credential descriptor contains no raw token values.
- **SC-002**: Unit tests prove host git clone/fetch receives a resolved launch-scoped credential helper when a descriptor is present.
- **SC-003**: Unit tests prove docker run and container launch payloads omit raw GitHub token values.
- **SC-004**: Unit tests prove missing or unresolvable GitHub credentials fail with redaction-safe operator messages.
- **SC-005**: Activity boundary tests prove the real `agent_runtime.launch_session` invocation shape carries a descriptor and not a raw token.
- **SC-006**: Traceability checks preserve MM-320 in spec, plan, tasks, verification output, and PR metadata.
