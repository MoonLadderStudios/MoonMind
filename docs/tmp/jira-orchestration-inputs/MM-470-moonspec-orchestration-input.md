# MM-470 MoonSpec Orchestration Input

## Source

- Jira issue: MM-470
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce authorized scope and operation-mode policy before launch
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-470 from MM project
Summary: Enforce authorized scope and operation-mode policy before launch
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-470 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-470: Enforce authorized scope and operation-mode policy before launch

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 9. Scope and authorization contract
  - 15. Security, isolation, and policy rules
- Coverage IDs:
  - DESIGN-REQ-004
  - DESIGN-REQ-005

User Story
As a security operator, I need MoonMind to validate scope, approvals, operation mode, target boundaries, and network requirements before PentestGPT starts so unauthorized or ambiguous security testing fails closed.

Acceptance Criteria
- A run cannot proceed without a readable approved scope artifact or equivalent approved envelope.
- Validation confirms scope expiration, target membership, runner-profile allowance, operation-mode compatibility, required network attachment approval, principal authorization, and recorded manual approval when required.
- `recon_only`, `validate_hypothesis`, and `full_authorized` are handled as explicit modes with no silent escalation or fallback into `full_authorized`.
- Ambiguous policy, scope, or approval state returns structured diagnostics and a fail-closed status before provider lease acquisition or container launch.

Requirements
- Represent authorized scope fields for targets, target class, allowed/prohibited actions, approvals, runner profiles, network requirements, and metadata.
- Perform scope and authorization validation as the first launch-pipeline step.
- Map policy failures to documented non-retryable errors such as `INVALID_SCOPE`, `PERMISSION_DENIED`, and `UNAPPROVED_TARGET`.

Relevant Implementation Notes
- Preserve MM-470 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for PentestGPT scope authorization and policy rules.
- Enforce scope and authorization validation before provider lease acquisition or container launch.
- Keep operation modes explicit and fail closed for unsupported, ambiguous, expired, unauthorized, or unapproved scope state.
- Return structured diagnostics for policy failures and map them to the documented non-retryable error codes.

Non-Goals
- Implementing the full PentestGPT runner execution path beyond pre-launch scope and policy enforcement.
- Silently escalating operation modes or falling back to `full_authorized`.
- Treating unreadable, expired, ambiguous, or missing approval state as authorized.

Validation
- Verify runs fail closed without a readable approved scope artifact or equivalent approved envelope.
- Verify validation covers scope expiration, target membership, runner-profile allowance, operation-mode compatibility, network attachment approval, principal authorization, and manual approval requirements.
- Verify `recon_only`, `validate_hypothesis`, and `full_authorized` remain explicit modes with no silent escalation.
- Verify validation runs before provider lease acquisition or container launch.
- Verify policy failures produce structured diagnostics and documented non-retryable error codes.

Needs Clarification
- None
