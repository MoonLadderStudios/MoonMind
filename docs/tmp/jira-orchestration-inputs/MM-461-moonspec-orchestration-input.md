# MM-461 MoonSpec Orchestration Input

## Source

- Jira issue: MM-461
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Report Bundle Workflow Publishing
- Labels: `moonmind-workflow-mm-ba49b1c2-6312-465a-bf68-0d46b37886cf`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-461 from MM project
Summary: Report Bundle Workflow Publishing
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-461 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-461: Report Bundle Workflow Publishing

Short Name
report-bundle-workflow-publishing

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 3. Core decision, 6. Definitions, 7. Consumer and producer invariants, 9. Report bundle model, 11.2 Execution linkage, 11.3 Step-aware linkage, 16. Workflow integration guidance, 16.3 Finalization rule
- Coverage IDs: DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-018

User Story
As a workflow author, I can publish a report bundle from activities and return compact refs to workflow code, so report bodies and evidence remain durable without bloating workflow history.

Acceptance Criteria
- Given an activity creates a report bundle, then it writes each report component as an artifact and links it to namespace, workflow_id, run_id, link_type, and optional label.
- Given a report is step-scoped or iterative, then bounded step metadata such as step_id, attempt, and scope is attached without embedding report content in workflow history.
- Given a report is the final deliverable, then exactly one canonical final report is identifiable via report.primary, metadata.is_final_report = true, and metadata.report_scope = final.
- Given evidence such as screenshots, command results, transcripts, excerpts, or structured findings exists, then it remains separately addressable instead of being buried only inside a rendered report.
- Workflow return values and persisted workflow state contain artifact_ref_v/artifact_id refs and bounded counts, not report bodies, evidence blobs, logs, screenshots, or raw download URLs.

Requirements
- Standardize a compact `report_bundle_v = 1` result shape with refs for `primary_report_ref`, `summary_ref`, `structured_ref`, `evidence_refs`, `report_type`, `report_scope`, `sensitivity`, and bounded counts.
- Keep report body, finding details, screenshots, logs, transcripts, and evidence artifact-backed.
- Make activities responsible for assembling report content, writing artifacts, linking artifacts, and returning compact bundles.
- Support execution-level and step-aware report linkage using existing artifact link semantics.

Relevant Implementation Notes
- Preserve MM-461 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Artifacts/ReportArtifacts.md` as the source design reference for report bundle shape, report artifact definitions, producer and consumer invariants, execution linkage, step-aware linkage, and final report identification.
- Scope implementation to activity-side report bundle publication and compact workflow return values; do not embed report bodies, evidence blobs, logs, screenshots, transcripts, raw download URLs, or large structured findings in workflow history or persisted workflow state.
- Report bundle publication should write each report component as an artifact, link it to the producing execution with namespace, workflow_id, run_id, link_type, and optional label, and return only artifact refs and bounded metadata to workflow code.
- Step-scoped or iterative reports should attach bounded step metadata such as step_id, attempt, and scope through artifact metadata or link metadata rather than inline report content.
- Final deliverables must make exactly one canonical final report identifiable through `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
- Evidence artifacts should remain separately addressable from rendered report content so screenshots, command results, transcripts, excerpts, and structured findings can be inspected independently.

Validation
- Verify an activity can create a report bundle by writing report components as artifacts and linking them to namespace, workflow_id, run_id, link_type, and optional label.
- Verify compact workflow returns include `artifact_ref_v` or artifact ID refs plus bounded counts, and exclude report bodies, evidence blobs, logs, screenshots, raw download URLs, and large finding details.
- Verify step-scoped and iterative report bundles preserve bounded step metadata without embedding report content in workflow history.
- Verify final report bundles expose exactly one canonical final report through `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
- Verify evidence such as screenshots, command results, transcripts, excerpts, and structured findings remains separately addressable through artifact refs.

Needs Clarification
- None

Dependencies
- Trusted Jira link metadata at fetch time shows MM-461 blocks MM-460, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-461 is blocked by MM-462, whose embedded status is Backlog.
