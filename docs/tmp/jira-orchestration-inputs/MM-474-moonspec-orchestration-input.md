# MM-474 MoonSpec Orchestration Input

## Source

- Jira issue: MM-474
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish PentestGPT artifacts, evidence, Live Logs, and normalized findings
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-474 from MM project
Summary: Publish PentestGPT artifacts, evidence, Live Logs, and normalized findings
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-474 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-474: Publish PentestGPT artifacts, evidence, Live Logs, and normalized findings

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 13. Workspace and artifact contract
  - 14. Normalized findings contract
  - 15.4 Redaction and artifact hygiene
  - 16.3 Heartbeats and progress
- Coverage IDs:
  - DESIGN-REQ-015
  - DESIGN-REQ-016
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-019
  - DESIGN-REQ-020

User Story
As a security operator, I need PentestGPT output published as durable artifacts, MoonMind-native Live Logs, and normalized findings so audit, remediation, dashboards, and follow-up automation consume stable results instead of raw terminal transcripts.

Acceptance Criteria
- Published artifacts include input.manifest, input.instructions, runtime.stdout, runtime.stderr, runtime.diagnostics, output.summary, output.primary, output.provider_snapshot when available, optional output.logs, and a restricted evidence bundle.
- The normalized findings artifact follows PentestFindingSet fields and includes findings_count, confirmed_findings_count, and high_or_critical_count.
- Downstream-facing summaries and reports use normalized findings rather than uncontrolled raw transcripts as the primary output.
- Finding confidence values are limited to suspected, supported, and confirmed and are derived conservatively.
- Live Logs present merged stdout/stderr plus MoonMind annotations and are not a terminal emulator.
- Secret-like values are redacted from human-facing summaries and provider credentials are absent from durable artifacts.
- session.summary, session.step_checkpoint, session.control_event, and session.reset_boundary are not published by default.

Requirements
- Store large logs, findings, evidence, and exports as durable artifacts rather than workflow history payloads.
- Publish normalized findings as canonical result truth and raw transcripts as restricted evidence.
- Emit structured progress annotations and heartbeats for operator visibility.

Relevant Implementation Notes
- Preserve MM-474 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for the PentestGPT workspace and artifact contract, normalized findings contract, redaction and artifact hygiene, and heartbeat/progress behavior.
- Publish durable artifact records for input.manifest, input.instructions, runtime.stdout, runtime.stderr, runtime.diagnostics, output.summary, output.primary, output.provider_snapshot when available, optional output.logs, and a restricted evidence bundle.
- Store large logs, findings, evidence, exports, and raw transcripts as durable artifacts rather than workflow history payloads.
- Publish normalized findings as the canonical result truth using the PentestFindingSet contract.
- Include findings_count, confirmed_findings_count, and high_or_critical_count in normalized finding summaries.
- Make downstream-facing summaries, reports, dashboards, remediation flows, and follow-up automation consume normalized findings rather than uncontrolled raw terminal transcripts as their primary output.
- Derive finding confidence conservatively and limit values to suspected, supported, and confirmed.
- Present Live Logs as merged stdout/stderr plus MoonMind annotations, not as a terminal emulator.
- Emit structured progress annotations and heartbeats for operator visibility.
- Redact secret-like values from human-facing summaries and keep provider credentials out of durable artifacts.
- Do not publish session.summary, session.step_checkpoint, session.control_event, or session.reset_boundary by default.

Non-Goals
- Implementing PentestGPT provider profile selection, lease coordination, cooldown handling, wrapper launch, or instruction materialization beyond the published artifact inputs and outputs owned by MM-474.
- Treating raw terminal transcripts as the canonical downstream result surface.
- Exposing Live Logs as a terminal emulator or interactive attachment surface.
- Publishing session.summary, session.step_checkpoint, session.control_event, or session.reset_boundary by default.
- Persisting provider credentials or secret-like values in durable artifacts or human-facing summaries.

Validation
- Verify all required artifact names are published with durable artifact records and appropriate restricted evidence handling.
- Verify large logs, findings, evidence, exports, and raw transcripts are stored as durable artifacts rather than workflow history payloads.
- Verify normalized findings follow the PentestFindingSet contract and include findings_count, confirmed_findings_count, and high_or_critical_count.
- Verify downstream-facing summaries and reports use normalized findings as the primary output.
- Verify finding confidence values are limited to suspected, supported, and confirmed and are derived conservatively.
- Verify Live Logs present merged stdout/stderr plus MoonMind annotations and do not behave as a terminal emulator.
- Verify structured progress annotations and heartbeats are emitted for operator visibility.
- Verify secret-like values are redacted from human-facing summaries and provider credentials are absent from durable artifacts.
- Verify session.summary, session.step_checkpoint, session.control_event, and session.reset_boundary are not published by default.

Needs Clarification
- None
