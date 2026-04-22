# MM-464 MoonSpec Orchestration Input

## Source

- Jira issue: MM-464
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Report Workflow Rollout and Examples
- Labels: `moonmind-workflow-mm-ba49b1c2-6312-465a-bf68-0d46b37886cf`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-464 from MM project
Summary: Report Workflow Rollout and Examples
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-464 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-464: Report Workflow Rollout and Examples

Short Name
report-workflow-rollout-examples

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 17. Example workflow mappings, 18. Suggested API/UI extensions, 19. Migration guidance, 20. Open questions
- Coverage IDs: DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022

User Story
As a MoonMind maintainer, I can migrate report-producing workflow families incrementally to the report artifact contract with documented examples and graceful fallback for existing generic outputs.

Acceptance Criteria
- Unit-test, coverage, pentest/security, and benchmark report examples each map to `report.primary` and appropriate summary, structured, evidence, runtime, and diagnostic artifacts.
- New report-producing workflows prefer `report.*` semantics while existing generic `output.primary` flows continue to operate.
- The UI degrades gracefully when an execution has only generic output artifacts and no `report.primary` artifact.
- Migration docs or examples distinguish curated reports, evidence, runtime stdout/stderr, and diagnostics for each workflow family.
- Convenience report fields or endpoints, if introduced during rollout, remain projections over the normal artifact APIs.

Requirements
- Document and validate report mappings for unit-test, coverage, pentest/security, and benchmark workflows.
- Support incremental rollout phases: metadata conventions, explicit `report.*` link types and UI surfacing, compact report bundle contract, and optional projections, filters, retention, and pinning.
- Keep existing generic outputs available during migration.
- Preserve clear separation between curated reports and observability artifacts in examples and tests.

Relevant Implementation Notes
- Use the existing artifact APIs as the baseline for the first rollout; report-aware convenience surfaces must remain projections over normal artifacts.
- Represent report-producing workflow examples with explicit artifact families, including `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `runtime.stdout`, `runtime.stderr`, and `runtime.diagnostics` where applicable.
- Include example mappings for unit test reports, coverage reports, pentest or security reports, and benchmark or evaluation reports.
- Keep report metadata bounded to display and classification fields such as `artifact_type`, `producer`, `subject`, `finding_counts`, `severity_counts`, and `sensitivity` where relevant.
- Preserve fallback behavior for executions that only have generic output artifacts and no `report.primary` artifact.
- Treat suggested execution detail fields and any report projection endpoint as optional read models over the underlying artifact references, not as a second storage model.
- Preserve MM-464 and coverage IDs DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, and DESIGN-REQ-022 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- Requiring a flag-day migration for existing report-producing workflows.
- Replacing generic `output.primary` behavior for existing workflows during rollout.
- Creating a second report storage model or making report convenience fields authoritative over artifact records.
- Forcing immediate implementation of every optional API/UI extension or open question from the source document.
- Collapsing curated reports, evidence, runtime stdout/stderr, diagnostics, and other observability artifacts into one undifferentiated output.

Validation
- Verify unit-test, coverage, pentest/security, and benchmark examples map to `report.primary` and the appropriate related report, runtime, and diagnostic artifacts.
- Verify new report-producing workflow guidance prefers explicit `report.*` semantics while existing generic `output.primary` flows remain valid.
- Verify UI or documentation guidance describes graceful fallback when an execution has generic outputs but no `report.primary` artifact.
- Verify migration guidance distinguishes curated reports, evidence, runtime stdout/stderr, and diagnostics for each workflow family.
- Verify any introduced convenience fields or report endpoint behavior is documented and implemented as a projection over normal artifact APIs.

Needs Clarification
- None
