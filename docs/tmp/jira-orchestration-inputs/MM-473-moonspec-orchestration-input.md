# MM-473 MoonSpec Orchestration Input

## Source

- Jira issue: MM-473
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Materialize instructions and run the non-interactive PentestGPT wrapper
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-473 from MM project
Summary: Materialize instructions and run the non-interactive PentestGPT wrapper
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-473 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-473: Materialize instructions and run the non-interactive PentestGPT wrapper

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 12. Launch and materialization pipeline
  - 12.2 Instruction bundle
  - 12.3 MoonMind wrapper entrypoint
  - 12.4 Command construction
  - 12.5 No direct upstream attach workflow
- Coverage IDs:
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014

User Story
As a security operator, I need MoonMind to create a run-scoped instruction bundle and invoke PentestGPT non-interactively through the MoonMind wrapper so execution respects scope boundaries without exposing full instructions or secrets in process-visible fields.

Acceptance Criteria
- The instruction bundle includes objective, scope boundaries, target summary, operation mode, evidence requirements, stop conditions, prohibited actions, and reporting expectations.
- The command and container metadata contain only the instruction file path, never the full instruction text.
- The wrapper enforces non-interactive execution and telemetry-off defaults unless an explicit auditable deployment policy override exists.
- Runtime output paths for stdout, stderr, diagnostics, raw evidence, and normalizer inputs are deterministic under the run-scoped workspace.
- Ordinary PentestGPT execution never uses upstream make connect, docker attach, TTY attachment, or public terminal attachment UX.

Requirements
- Materialize workspace paths, artifact directories, secrets, provider environment, instruction inputs, and optional network attachments in the documented order.
- Use a MoonMind-owned wrapper entrypoint to prepare inputs, validate environment, invoke PentestGPT, capture exit metadata, and export raw evidence.
- Keep telemetry disabled by default through both profile env and wrapper command behavior.

Relevant Implementation Notes
- Preserve MM-473 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for the PentestGPT launch and materialization pipeline, instruction bundle, MoonMind wrapper entrypoint, command construction, and no-direct-attach workflow.
- Create a run-scoped instruction bundle that includes objective, scope boundaries, target summary, operation mode, evidence requirements, stop conditions, prohibited actions, and reporting expectations.
- Pass only the instruction file path through process-visible command arguments and container metadata; never pass full instruction text in command strings, labels, environment variables, workflow history, durable artifacts, logs, or UI responses.
- Use a MoonMind-owned wrapper entrypoint to prepare inputs, validate environment, invoke PentestGPT non-interactively, capture exit metadata, and export raw evidence.
- Enforce telemetry-off defaults through both profile environment and wrapper command behavior unless an explicit auditable deployment policy override exists.
- Materialize deterministic runtime output paths for stdout, stderr, diagnostics, raw evidence, and normalizer inputs under the run-scoped workspace.
- Preserve the required launch order: materialize workspace paths, artifact directories, secrets, provider environment, instruction inputs, and optional network attachments in the documented order before invoking the wrapper.
- Do not use upstream `make connect`, `docker attach`, TTY attachment, or public terminal attachment UX for ordinary PentestGPT execution.
- Keep secrets and full instructions out of process-visible fields and durable records while still producing deterministic diagnostics and evidence references.

Non-Goals
- Implementing PentestGPT artifact publishing, Live Logs, normalized findings, or final evidence bundle behavior beyond deterministic output paths and raw evidence export owned by MM-473.
- Implementing provider profile selection, lease coordination, cooldown handling, or runner profile policy beyond consuming the already materialized environment and workspace inputs required by this story.
- Supporting interactive upstream attach flows, public terminal attachment UX, or hidden runtime prompts for ordinary PentestGPT execution.
- Persisting raw secrets, full instruction text, or resolved provider credentials in workflow history, durable artifacts, container labels, process arguments, logs, or UI responses.

Validation
- Verify the instruction bundle includes objective, scope boundaries, target summary, operation mode, evidence requirements, stop conditions, prohibited actions, and reporting expectations.
- Verify command arguments and container metadata contain only the instruction file path and never the full instruction text.
- Verify the MoonMind-owned wrapper entrypoint prepares inputs, validates environment, invokes PentestGPT non-interactively, captures exit metadata, and exports raw evidence.
- Verify telemetry is disabled by default through both profile environment and wrapper command behavior unless an explicit auditable deployment policy override exists.
- Verify runtime output paths for stdout, stderr, diagnostics, raw evidence, and normalizer inputs are deterministic under the run-scoped workspace.
- Verify workspace paths, artifact directories, secrets, provider environment, instruction inputs, and optional network attachments are materialized in the documented order.
- Verify ordinary PentestGPT execution never uses upstream `make connect`, `docker attach`, TTY attachment, or public terminal attachment UX.
- Verify secrets and full instruction text do not appear in workflow history, durable artifacts, container labels, process arguments, logs, or UI responses.

Needs Clarification
- None
