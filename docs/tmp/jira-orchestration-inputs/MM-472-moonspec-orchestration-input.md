# MM-472 MoonSpec Orchestration Input

## Source

- Jira issue: MM-472
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Resolve PentestGPT Provider Profiles and coordinate leases
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-472 from MM project
Summary: Resolve PentestGPT Provider Profiles and coordinate leases
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-472 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-472: Resolve PentestGPT Provider Profiles and coordinate leases

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 11. Provider profile model
  - 12. Launch and materialization pipeline
- Coverage IDs:
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-023

User Story
As a platform operator, I need PentestGPT credential selection to use Provider Profiles with slot leases and cooldown handling so API keys, local providers, quota limits, and non-interactive auth are shaped consistently with the rest of MoonMind.

Acceptance Criteria
- PentestGPT profiles use `runtime_id = pentestgpt` and support Anthropic, OpenRouter, and local OpenAI-compatible canonical shapes.
- Exact profile refs take precedence; otherwise selector resolution filters by runtime, provider selector, enabled state, cooldown, available slots, and priority.
- Only allowlisted environment keys are materialized, conflicting provider keys are cleared, and `LANGFUSE_ENABLED` defaults to `false`.
- A provider-profile slot lease is acquired before workload launch and released on all terminal code paths.
- Provider 429 or quota errors mark cooldown and return structured diagnostics rather than uncontrolled retry loops.
- Future OAuth-backed profile support is represented only as an explicit future path, not a hidden runtime prompt or terminal flow.

Requirements
- Use existing Provider Profile orchestration rather than bespoke credential lookup.
- Keep provider credentials out of durable artifacts and labels.
- Force non-interactive command behavior through provider profile materialization.

Relevant Implementation Notes
- Preserve MM-472 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for the PentestGPT provider profile model plus launch and materialization pipeline.
- Model PentestGPT provider profiles under `runtime_id = pentestgpt`.
- Support canonical Anthropic API-key, OpenRouter, and local OpenAI-compatible profile shapes.
- Keep optional future OAuth-backed support explicit and out-of-band through the existing OAuth session architecture; ordinary PentestGPT runs must remain non-interactive.
- Resolve profiles by exact `execution_profile_ref` first, then by compatible selector filtering on runtime, provider, enabled state, cooldown, available slots, and priority.
- Acquire a provider-profile slot lease before workload launch and release it on every terminal path, including success, failure, cancellation, and quota/cooldown exits.
- On provider 429 or quota failure, mark cooldown for the selected profile, release the lease, and return structured diagnostics instead of entering uncontrolled retry loops.
- Materialize only allowlisted provider environment keys for the selected profile and clear conflicting provider keys.
- Default `LANGFUSE_ENABLED` to `false` unless policy explicitly overrides it.
- Keep provider credentials out of workflow history, durable artifacts, container labels, process arguments, logs, and UI responses.
- Preserve the required launch order from the Pentest design: resolve provider profile, acquire profile lease, resolve runner profile, materialize task paths, materialize secret refs and provider environment, then launch the workload through MoonMind's Docker boundary.

Non-Goals
- Implementing the PentestGPT wrapper instruction bundle or runner profile policy beyond the provider-profile selection, lease, cooldown, and materialization concerns owned by MM-472.
- Adding a hidden interactive OAuth prompt or terminal flow to ordinary PentestGPT task execution.
- Creating a bespoke PentestGPT credential lookup path outside the existing Provider Profile orchestration model.
- Persisting raw provider credentials, API keys, or resolved secret values in durable records or labels.

Validation
- Verify PentestGPT profile definitions use `runtime_id = pentestgpt`.
- Verify Anthropic, OpenRouter, and local OpenAI-compatible canonical profile shapes are supported.
- Verify exact profile refs take precedence over selector-based resolution.
- Verify selector-based resolution filters by runtime, provider selector, enabled state, cooldown, available slots, and priority.
- Verify only allowlisted environment keys are materialized, conflicting provider keys are cleared, and `LANGFUSE_ENABLED` defaults to `false`.
- Verify a provider-profile slot lease is acquired before workload launch.
- Verify provider-profile slot leases are released on success, failure, cancellation, and cooldown/quota terminal paths.
- Verify provider 429 or quota errors mark profile cooldown and produce structured diagnostics without uncontrolled retry loops.
- Verify future OAuth-backed profile support remains an explicit future path and does not introduce hidden runtime prompts.
- Verify provider credentials do not appear in workflow history, durable artifacts, container labels, process arguments, logs, or UI responses.

Needs Clarification
- None
