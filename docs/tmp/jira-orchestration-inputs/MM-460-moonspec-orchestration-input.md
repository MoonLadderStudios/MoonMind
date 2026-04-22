# MM-460 MoonSpec Orchestration Input

## Source

- Jira issue: MM-460
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Report Artifact Contract
- Labels: `moonmind-workflow-mm-ba49b1c2-6312-465a-bf68-0d46b37886cf`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-460 from MM project
Summary: Report Artifact Contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-460 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-460: Report Artifact Contract

Short Name
report-artifact-contract

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 1. Purpose, 1.1 Related docs and ownership boundaries, 2. Scope / Non-goals, 3. Core decision, 5. Non-goals, 8. Recommended report artifact classes, 10. Metadata model for report artifacts, 11. Storage and linkage rules, 21. Bottom line
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-009

User Story
As a workflow producer, I can publish report deliverables using explicit report artifact link types and bounded metadata in the existing artifact system, so reports become first-class without creating a separate storage plane.

Acceptance Criteria
- Given a report-producing workflow publishes a canonical report, when the artifact is linked, then it uses `link_type = report.primary` and remains stored in the existing artifact store.
- Given summary, structured results, evidence, appendix, findings-index, or export artifacts are part of a report deliverable, then they use the corresponding `report.*` link type instead of generic output classes.
- Given report metadata is stored, then only bounded display and classification fields such as `artifact_type`, `report_type`, `report_scope`, `title`, `producer`, `subject`, `render_hint`, `counts`, `step_id`, and `attempt` are accepted for control-plane use.
- Given metadata contains secrets, raw access grants, cookies, session tokens, or large inline payloads, then publication rejects or redacts those fields according to the existing artifact boundary.
- Generic `output.primary`, `output.summary`, and `output.agent_result` flows continue to work for non-report deliverables.

Requirements
- Define stable report link types for `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export`.
- Represent reports as artifact families in the existing artifact system, not as a new storage system.
- Standardize bounded report metadata keys while keeping detailed findings and large content in artifacts.
- Keep report bodies and large supporting evidence artifact-backed instead of embedding them in workflow history or control-plane metadata.
- Keep report semantics explicit without breaking generic output flows.

Relevant Implementation Notes
- Layer report-specific behavior on top of `docs/Temporal/WorkflowArtifactSystemDesign.md` and `docs/Temporal/ArtifactPresentationContract.md`; do not redefine artifact identity, storage, authorization, lifecycle, generic preview behavior, or rendering rules.
- Use `report.*` link types only when an artifact is explicitly part of a report deliverable.
- Continue using `output.primary`, `output.summary`, and `output.agent_result` for generic non-report outputs.
- Store report artifacts in the existing artifact store and index with immutable artifact IDs and ordinary execution linkage.
- Link report artifacts to the producing execution using standard execution references and `link_type` values such as `report.primary`, `report.summary`, `report.structured`, and `report.evidence`.
- Keep standardized metadata bounded and safe for control-plane display; detailed findings belong in `report.structured` or other linked artifacts.
- Preserve separation between curated reports and observability surfaces such as stdout, stderr, merged logs, diagnostics, provider snapshots, and session continuity artifacts.

Non-Goals
- Introducing a separate report blob store or replacing generic artifact APIs with a report-specific storage system.
- Treating every `output.primary` artifact as a report.
- Storing evidence, logs, diagnostics, or large report bodies inline inside one payload by default.
- Introducing mutable in-place report updates.
- Requiring every report workflow to produce PDF output.
- Making Mission Control parse provider-native raw payloads as canonical reports.
- Implementing provider-specific report-generation prompts, PDF rendering, full-text indexing, legal/compliance review, or mutable report update workflows.

Validation
- Verify report-producing workflows can publish a canonical `report.primary` artifact stored in the existing artifact system.
- Verify report bundle artifacts use explicit `report.*` link types for primary report, summary, structured results, evidence, appendix, findings index, and export outputs.
- Verify bounded report metadata accepts only safe display and classification keys and rejects or redacts secrets, raw grants, cookies, session tokens, and large inline payloads.
- Verify generic `output.primary`, `output.summary`, and `output.agent_result` flows continue to work for non-report deliverables.
- Verify report artifacts remain linked through existing execution artifact references without creating a separate storage plane.

Needs Clarification
- None
