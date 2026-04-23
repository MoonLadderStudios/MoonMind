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
