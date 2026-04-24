# MM-496 MoonSpec Orchestration Input

## Source

- Jira issue: MM-496
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose report-aware execution projections
- Labels:
  - `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-496 from MM project
Summary: Expose report-aware execution projections
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-496 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-496: Expose report-aware execution projections

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections:
  - 11.4 Latest report semantics
  - 18 Suggested API/UI extensions
  - 20 Open questions
- Coverage IDs:
  - DESIGN-REQ-013
  - DESIGN-REQ-022
  - DESIGN-REQ-024

User Story
As an API consumer, I want execution detail responses and an optional report projection to expose the latest report refs and bounded counts so clients can build report-first views without duplicating artifact-selection heuristics.

Acceptance Criteria
- Execution detail data can expose `has_report`, `latest_report_ref`, `latest_report_summary_ref`, `report_type`, `report_status`, and bounded finding/severity counts.
- A report projection endpoint, if implemented in this story, returns `execution_ref`, `primary_report_ref`, `summary_ref`, `structured_ref`, `evidence_refs`, `report_type`, and bounded counts over standard artifacts.
- Projection output never becomes a second report storage model; underlying artifacts remain individually addressable.
- Latest report selection is resolved server-side using execution identity and report link types.
- Restricted artifacts still obey artifact authorization and preview/default-read behavior.
- The story records whether the projection endpoint is implemented immediately or deferred in favor of execution-detail summary fields.

Requirements
- Provide report-aware server selection for clients.
- Keep projection data as a convenience read model over artifacts.
- Make unresolved projection timing explicit.

Relevant Implementation Notes
- Preserve MM-496 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Artifacts/ReportArtifacts.md` as the source design reference for latest-report semantics, projection-friendly API/UI extensions, and the open question around when projection behavior should ship.
- Add report-aware execution detail fields that expose the latest canonical report refs and bounded counts without requiring clients to re-run artifact selection heuristics.
- If a projection endpoint is implemented in this story, keep it as a convenience read model over standard artifacts rather than a second report storage model.
- Resolve latest report selection server-side from execution identity and report link types.
- Keep restricted-artifact authorization and preview/default-read behavior intact for any report projection or execution-detail exposure.
- Make the implementation decision explicit if the projection endpoint is deferred in favor of execution-detail summary fields.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-496 is blocked by MM-497, whose embedded status is Selected for Development.
- Trusted Jira link metadata at fetch time shows MM-496 is related to MM-495 via a Blocks link where MM-496 blocks MM-495; MM-495 is not a blocker for this story.

Validation
- Verify execution detail responses expose `has_report`, `latest_report_ref`, `latest_report_summary_ref`, `report_type`, `report_status`, and bounded finding/severity counts when canonical reports exist.
- Verify any projection endpoint added by this story returns `execution_ref`, `primary_report_ref`, `summary_ref`, `structured_ref`, `evidence_refs`, `report_type`, and bounded counts over standard artifacts.
- Verify projection output remains a convenience read model and does not become a second report storage model.
- Verify latest report selection is resolved server-side from execution identity and report link types.
- Verify restricted artifacts continue to obey artifact authorization and preview/default-read behavior.
- Verify the implementation records whether projection behavior ships now or is deferred in favor of execution-detail summary fields.

Needs Clarification
- None
