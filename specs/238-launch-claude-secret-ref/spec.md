# Feature Specification: Launch Claude Secret Ref

**Feature Branch**: `238-launch-claude-secret-ref`
**Created**: 2026-04-22
**Status**: Draft
**Input**:

```text
Jira issue: MM-448 from MM project
Summary: Launch Claude Code from the secret_ref provider profile
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-448 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-448: Launch Claude Code from the secret_ref provider profile

Source Reference
Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
Source Sections:
- 4. Desired profile shape
- 8. Runtime launch behavior
- 10.2 Backend
- 11. Final recommendation
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-013

User Story
As MoonMind launching a Claude Code managed runtime, I can resolve the claude_anthropic provider profile, clear conflicting environment keys, resolve the managed secret, inject ANTHROPIC_API_KEY, and start claude_code without adding a new runtime-selection model.

Acceptance Criteria
- claude_anthropic launches through the existing profile-driven materialization path.
- ANTHROPIC_API_KEY is injected from the managed secret referenced by anthropic_api_key.
- ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, and OPENAI_API_KEY are cleared before launch when configured.
- No new runtime-selection concept is introduced.
- Missing or unreadable secret bindings produce actionable failure output without exposing secret values.

Requirements
- Runtime launch must resolve provider profile, apply clear_env_keys, resolve secret_refs, inject ANTHROPIC_API_KEY, and launch claude_code.
- Workflow or activity payloads must carry compact refs/metadata rather than raw token values.

Implementation Notes
- Preserve MM-448 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for provider-profile-backed Claude Anthropic runtime launch behavior.
- Scope the implementation to launching Claude Code from the existing `claude_anthropic` profile-driven materialization path.
- Resolve the `claude_anthropic` provider profile and use its `credential_source=secret_ref` and `runtime_materialization_mode=api_key_env` shape.
- Resolve the managed secret referenced by `anthropic_api_key` and inject only `ANTHROPIC_API_KEY` into the Claude Code runtime environment.
- Apply `clear_env_keys` before launch so conflicting `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `OPENAI_API_KEY` values are removed when configured.
- Keep raw secret values out of workflow/activity payloads, logs, diagnostics, artifacts, and generated MoonSpec context; carry only compact refs and secret-free metadata.
- Missing, unreadable, unauthorized, or malformed secret bindings must fail with actionable, secret-free diagnostics.
- Do not introduce a new runtime-selection model or fork launch behavior away from existing provider profile materialization.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-448 blocks MM-447, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-448 is blocked by MM-449, whose embedded status is Selected for Development.

Needs Clarification
- None
```

## Classification

Single-story runtime feature request. The brief contains one independently testable managed-runtime launch behavior: when MoonMind launches Claude Code through the existing provider-profile materialization path, the `claude_anthropic` profile resolves a managed secret reference, clears configured conflicting environment keys, injects only the Anthropic API key environment value, and starts Claude Code without adding a new runtime-selection model.

## User Story - Launch Claude From Secret Ref Profile

**Summary**: As MoonMind launching a Claude Code managed runtime, I want the existing provider-profile materialization path to use the `claude_anthropic` secret-reference profile so that Claude starts with the right Anthropic credential and no conflicting environment values.

**Goal**: Claude Code managed runtime launches consume the existing `claude_anthropic` provider profile, resolve its managed secret binding, clear configured conflicts, inject `ANTHROPIC_API_KEY`, and report actionable secret-free failures when the binding cannot be used.

**Independent Test**: Configure a launch profile for `claude_anthropic` with a valid `anthropic_api_key` managed secret reference, conflicting Anthropic/OpenAI environment values, and `api_key_env` materialization, then invoke the managed-runtime launch materialization path and verify the resulting Claude Code environment contains only the resolved `ANTHROPIC_API_KEY`, omits configured conflicts, carries no raw secret in durable payloads or diagnostics, and fails with a secret-free actionable message when the binding is missing or unreadable.

**Acceptance Scenarios**:

1. **SCN-001**: **Given** a launch uses the `claude_anthropic` provider profile with a readable managed secret bound as `anthropic_api_key`, **When** MoonMind prepares Claude Code runtime launch materialization, **Then** it resolves the profile through the existing provider-profile path, resolves the secret reference, injects `ANTHROPIC_API_KEY`, and starts launch preparation without requiring a new runtime-selection model.
2. **SCN-002**: **Given** the profile defines `clear_env_keys` for `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `OPENAI_API_KEY`, **When** MoonMind prepares the runtime environment, **Then** those configured values are absent from the final Claude Code environment even if they were present in the inherited environment.
3. **SCN-003**: **Given** launch preparation resolves a managed secret for `ANTHROPIC_API_KEY`, **When** workflow or activity payloads, diagnostics, logs, artifacts, or generated MoonSpec context are produced, **Then** they contain compact references or metadata only and never include the raw secret value.
4. **SCN-004**: **Given** the `anthropic_api_key` binding is missing, unauthorized, malformed, or unreadable, **When** MoonMind prepares Claude Code launch materialization, **Then** launch fails before starting the runtime with a secret-free actionable error that identifies the unusable binding class.
5. **SCN-005**: **Given** existing non-Claude runtime profiles continue to launch through provider-profile materialization, **When** `claude_anthropic` secret-reference launch support is added, **Then** no new global runtime selector or alternate launch path is required.

### Edge Cases

- The provider profile references `anthropic_api_key` but the managed secret record no longer exists.
- The secret resolver rejects the binding due to authorization or malformed reference syntax.
- The inherited environment contains both conflicting Anthropic/OpenAI values and unrelated environment variables.
- The profile is otherwise valid but omits one of the recommended `clear_env_keys`.
- The launch fails after materialization and diagnostic output must still avoid exposing the resolved secret value.

## Assumptions

- `claude_anthropic` is the selected profile for this story; adding support for additional Claude Anthropic profile variants is out of scope unless they already use the same provider-profile contract.
- Runtime launch preparation can be verified at the materialization or adapter boundary without invoking the real Claude Code binary.

## Source Design Requirements

- **DESIGN-REQ-006** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 4): The `claude_anthropic` profile SHOULD target `credential_source=secret_ref`, `runtime_materialization_mode=api_key_env`, a managed secret binding named `anthropic_api_key`, configured conflict-clearing keys, and an `ANTHROPIC_API_KEY` environment template. Scope: in scope, mapped to FR-001 through FR-007.
- **DESIGN-REQ-013** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 8): Runtime launch MUST reuse profile-driven materialization by resolving the provider profile, applying `clear_env_keys`, resolving `secret_refs`, injecting `ANTHROPIC_API_KEY`, and launching `claude_code` without a new runtime-selection concept. Scope: in scope, mapped to FR-001 through FR-010.
- **DESIGN-REQ-014** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 10.2): Backend/provider profile state MUST keep runtime-visible profile data synced so launch uses the secret-reference profile shape and returns or reports only secret-free readiness and failure metadata. Scope: in scope, mapped to FR-004, FR-008, FR-009, FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST launch Claude Code through the existing provider-profile materialization path when the selected profile is `claude_anthropic`.
- **FR-002**: The launch materialization path MUST recognize the `claude_anthropic` profile shape with `credential_source=secret_ref` and `runtime_materialization_mode=api_key_env`.
- **FR-003**: The launch materialization path MUST resolve the managed secret referenced by `secret_refs.anthropic_api_key`.
- **FR-004**: The final Claude Code runtime environment MUST receive the resolved credential only as `ANTHROPIC_API_KEY`.
- **FR-005**: The final Claude Code runtime environment MUST omit every configured key in the profile's `clear_env_keys` after materialization, including `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `OPENAI_API_KEY` when configured.
- **FR-006**: The system MUST NOT introduce a new runtime-selection concept, global runtime mode, or alternate Claude launch path for this profile.
- **FR-007**: Workflow and activity payloads related to the launch MUST carry compact profile or secret references and secret-free metadata rather than raw token values.
- **FR-008**: Logs, diagnostics, artifacts, failure summaries, and generated orchestration context MUST NOT contain the resolved Anthropic secret value.
- **FR-009**: Missing, malformed, unauthorized, or unreadable `anthropic_api_key` bindings MUST fail before runtime start with actionable secret-free error output.
- **FR-010**: Existing provider-profile launches for other runtimes MUST continue to use their current materialization semantics.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-448 and the source design mappings.

### Key Entities

- **Claude Anthropic Provider Profile**: The runtime-visible provider profile selected for Claude Code launch, including credential source, materialization mode, secret-reference binding, and environment-clearing policy.
- **Managed Secret Binding**: A compact reference from the profile to the stored Anthropic credential, represented to launch workflows without the raw secret value.
- **Launch Materialization Result**: The secret-bearing runtime environment prepared immediately before process start, plus secret-free metadata and diagnostics safe for durable storage.
- **Launch Failure Summary**: Secret-free diagnostic output describing why the binding could not be used.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Boundary tests confirm a `claude_anthropic` launch profile with `secret_refs.anthropic_api_key` produces a final runtime environment containing `ANTHROPIC_API_KEY`.
- **SC-002**: Boundary tests confirm configured conflicting keys are absent from the final Claude Code runtime environment after materialization.
- **SC-003**: Regression tests confirm launch payloads, failure summaries, logs, and artifacts inspected by tests contain no raw Anthropic secret value.
- **SC-004**: Failure-path tests confirm missing, malformed, unauthorized, or unreadable secret bindings produce actionable secret-free errors before runtime start.
- **SC-005**: Regression tests confirm no new runtime-selection model is required and existing provider-profile launch behavior for non-Claude profiles is unchanged.
- **SC-006**: Traceability verification confirms MM-448 and DESIGN-REQ-006, DESIGN-REQ-013, and DESIGN-REQ-014 remain present in MoonSpec artifacts and final verification evidence.
