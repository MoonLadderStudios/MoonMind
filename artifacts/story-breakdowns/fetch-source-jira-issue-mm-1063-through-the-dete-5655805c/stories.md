# Story Breakdown: MM-1063: Update Presets

Source: trusted Jira preset brief
Source document class: imperative-input
Extracted: 2026-06-30T22:50:39Z

## Design Summary
The trusted Jira brief describes a product-facing preset and verification upgrade: normalize breakdown preset naming, remove Jira Board input from Jira breakdowns, add GitHub Issue orchestration and breakdown composites, add GitHub story-output tooling, gate issue implementation flows with moonspec-verify, extend moonspec-verify for issue briefs, and enforce workflow/MoonSpec terminology. The work should remain provider-boundary aware, preserve source traceability, avoid inline implementation in breakdown composites, and prove behavior with targeted tests.

## Coverage Points
- DESIGN-REQ-001 (requirement): Normalize breakdown preset identities - Preset catalog entries use the requested user-facing titles and descriptions while preserving existing slugs unless a deliberate migration is added.
- DESIGN-REQ-002 (requirement): Remove Jira Board from Jira breakdown presets - Jira breakdown presets expose Jira Project Key and issue type but no jira_board_id input, Jira Board label, selected board copy, or storyOutput.jira.boardId.
- DESIGN-REQ-003 (constraint): Preserve Jira Breakdown and Implement behavior - The existing Jira Breakdown and Implement project-key strategy remains behaviorally intact while title and copy are normalized.
- DESIGN-REQ-004 (integration): Add GitHub Issue Orchestrate preset - A GitHub issue can drive MoonSpec orchestration, verification, remediation, doc reconciliation, PR creation, and issue status update.
- DESIGN-REQ-005 (integration): Add GitHub breakdown implement preset - Breakdown can create GitHub issues and enqueue dependent GitHub Issue Implement workflow executions.
- DESIGN-REQ-006 (integration): Add GitHub breakdown orchestrate preset - Breakdown can create GitHub issues and enqueue dependent GitHub Issue Orchestrate workflow executions with source traceability.
- DESIGN-REQ-007 (integration): Create GitHub story-output tools - Trusted story-output handlers create GitHub issues and downstream GitHub Issue Implement or Orchestrate workflow executions with stable mappings.
- DESIGN-REQ-008 (state-model): Preserve story order and dependency edges - GitHub issue mappings and downstream workflow executions preserve generated story order and dependsOn relationships.
- DESIGN-REQ-009 (requirement): Issue implement flows use moonspec-verify gate - Jira Implement and GitHub Issue Implement run moonspec-verify before PR creation or final issue lifecycle transition.
- DESIGN-REQ-010 (state-model): Verification remediation loop gates transitions - ADDITIONAL_WORK_NEEDED triggers bounded remediation and re-verification; terminal outcomes stop; PR/review/done transitions require FULLY_IMPLEMENTED except already-implemented no-code-change paths.
- DESIGN-REQ-011 (requirement): moonspec-verify supports issue-brief mode - moonspec-verify can verify Jira or GitHub issue briefs without requiring spec.md, plan.md, tasks.md, or constitution.md.
- DESIGN-REQ-012 (artifact): moonspec-verify emits structured JSON when requested - When verification_artifact_path is supplied, verification writes structured JSON with verdict, gaps, remainingWork, tests, and drift while still returning Markdown.
- DESIGN-REQ-013 (contract): Verifier verdict vocabulary is workflow-consumable - FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, NO_DETERMINATION, BLOCKED, and FAILED_UNRECOVERABLE are consistently documented; skill projection contamination remains diagnostic.
- DESIGN-REQ-014 (constraint): Terminology uses workflows and MoonSpec consistently - User-facing copy uses workflow/workflow execution for MoonMind queue entries, avoids invalid task terminology, and spells MoonSpec consistently.
- DESIGN-REQ-015 (requirement): Tests cover presets, tools, gating, remediation, and terminology - Tests validate seeding, expansion, Jira input simplification, GitHub story output, downstream workflow creation, verification gating, remediation loops, and terminology guardrails.

## Stories
### STORY-001: Normalize breakdown preset catalog and Jira inputs
As an operator choosing a breakdown workflow, I need the preset catalog to show the new Breakdown and Jira names and collect only Jira Project Key based inputs so Jira story creation is consistent across breakdown flows.

Independent test: Seed and expand the three Jira breakdown presets and assert exact titles, no jira_board_id or boardId, projectKey/issueTypeName story output, and unchanged Jira Breakdown and Implement downstream behavior.

Acceptance criteria:
- jira-breakdown title is exactly Breakdown and Jira Create.
- jira-breakdown-implement title is exactly Breakdown and Jira Implement.
- jira-breakdown-orchestrate title is exactly Breakdown and Jira Orchestrate.
- No Jira breakdown preset exposes Jira Board or storyOutput.jira.boardId.
- Jira issue creation uses projectKey and issueTypeName while existing Jira Breakdown and Implement behavior is preserved.

Requirements:
- Update preset YAML/seed metadata for existing Jira breakdown slugs.
- Remove Jira Board inputs and boardId output from Jira breakdown presets.
- Keep jira_project_key, jira_issue_type, jira_dependency_mode, source_issue_key, source_design_path, and feature_request inputs.

Owned coverage:
- DESIGN-REQ-001: STORY-001 owns normalize breakdown preset identities.
- DESIGN-REQ-002: STORY-001 owns remove jira board from jira breakdown presets.
- DESIGN-REQ-003: STORY-001 owns preserve jira breakdown and implement behavior.
- DESIGN-REQ-015: STORY-001 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: None
Assumptions:
- Existing Jira breakdown slugs remain stable because the source prefers preserving slugs.

### STORY-002: Add GitHub Issue Orchestrate preset
As an operator starting from a GitHub issue, I need a GitHub Issue Orchestrate preset that runs the full MoonSpec lifecycle with GitHub lifecycle updates and verification-gated PR creation.

Independent test: Seed and expand github-issue-orchestrate with a GitHub issue picker payload and assert it loads the brief, checks blockers, updates status, runs MoonSpec phases, gates remediation/PR/status transitions on verifier outcomes, and writes expected artifacts.

Acceptance criteria:
- github-issue-orchestrate exists with GitHub issue picker and manual reference fallback inputs.
- The preset runs issue brief loading, blocker checks, status updates, MoonSpec orchestration, verification, remediation, doc reconciliation, PR creation, and final GitHub status update in the required order.
- Terminal verifier outcomes stop before doc reconciliation, PR creation, or review updates.
- The already-implemented no-code-change path can complete consistently with GitHub Issue Implement.

Requirements:
- Create api_service/data/presets/github-issue-orchestrate.yaml.
- Use trusted GitHub issue brief, blocker, and status-update tools.
- Mirror Jira Orchestrate verification/remediation semantics for GitHub issues.

Owned coverage:
- DESIGN-REQ-004: STORY-002 owns add github issue orchestrate preset.
- DESIGN-REQ-010: STORY-002 owns verification remediation loop gates transitions.
- DESIGN-REQ-014: STORY-002 owns terminology uses workflows and moonspec consistently.
- DESIGN-REQ-015: STORY-002 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: STORY-005
Assumptions:
- Existing trusted GitHub tools remain the provider boundary.

### STORY-003: Create GitHub issue story-output tools
As a breakdown workflow, I need trusted story-output tools that turn eligible story candidates into GitHub issues and create downstream MoonMind workflow executions with stable mappings and conservative dependency semantics.

Independent test: Call the new GitHub story-output handlers with reconciled stories and assert eligible issues, skipped stories, issue mappings, explicit dependencyMode, and ordered dependsOn downstream workflow executions.

Acceptance criteria:
- story.create_github_issues is registered and creates one GitHub issue per eligible story.
- story.create_github_issue_implement_workflows creates GitHub Issue Implement workflow executions from issue mappings.
- story.create_github_issue_orchestrate_workflows creates GitHub Issue Orchestrate workflow executions from issue mappings.
- Outputs use workflow terminology and do not claim GitHub dependencies unless a trusted API result proves them.

Requirements:
- Read runtime story breakdown JSON.
- Reuse reconciliation behavior for fully implemented, partially implemented, and unverifiable stories.
- Preserve source path/title/claim/source issue traceability in GitHub issue bodies.
- Return stable issue and workflow mappings plus dependency mode/count.

Owned coverage:
- DESIGN-REQ-007: STORY-003 owns create github story-output tools.
- DESIGN-REQ-008: STORY-003 owns preserve story order and dependency edges.
- DESIGN-REQ-014: STORY-003 owns terminology uses workflows and moonspec consistently.
- DESIGN-REQ-015: STORY-003 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: None
Needs clarification:
- [NEEDS CLARIFICATION] Choose the first GitHub dependency mode: no dependency mode or body-based Blocked by/Blocks traceability.

### STORY-004: Add GitHub issue breakdown composite presets
As an operator with a broad source issue or design, I need Breakdown and GitHub Issue Implement and Breakdown and GitHub Issue Orchestrate presets that create GitHub issues from story candidates and enqueue dependent downstream workflows without implementing inline.

Independent test: Seed and expand both GitHub breakdown composite presets and assert source loading, moonspec-breakdown input preference, reconciliation, GitHub issue creation, ordered dependent workflow creation, downstream preset selection, source_design_path propagation, and partial outcome reporting.

Acceptance criteria:
- github-issue-breakdown-implement title is exactly Breakdown and GitHub Issue Implement.
- github-issue-breakdown-orchestrate title is exactly Breakdown and GitHub Issue Orchestrate.
- The implement composite creates downstream GitHub Issue Implement workflow executions.
- The orchestrate composite creates downstream GitHub Issue Orchestrate workflow executions.
- Neither composite performs implementation inline.

Requirements:
- Create api_service/data/presets/github-issue-breakdown-implement.yaml.
- Create api_service/data/presets/github-issue-breakdown-orchestrate.yaml.
- Preserve story order, dependencies, source issue, source document path, and traceability in downstream inputs.

Owned coverage:
- DESIGN-REQ-001: STORY-004 owns normalize breakdown preset identities.
- DESIGN-REQ-005: STORY-004 owns add github breakdown implement preset.
- DESIGN-REQ-006: STORY-004 owns add github breakdown orchestrate preset.
- DESIGN-REQ-008: STORY-004 owns preserve story order and dependency edges.
- DESIGN-REQ-014: STORY-004 owns terminology uses workflows and moonspec consistently.
- DESIGN-REQ-015: STORY-004 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: STORY-003, STORY-005

### STORY-005: Gate issue implementation with moonspec-verify
As an operator relying on Jira or GitHub Issue Implement, I need implementation, PR creation, and final issue lifecycle transitions to be gated by moonspec-verify with bounded remediation and re-verification when the verifier reports additional work.

Independent test: Expand Jira Implement and GitHub Issue Implement and simulate FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, exhausted remediation, and terminal environment verifier outcomes to assert PR/status gating behavior.

Acceptance criteria:
- Jira Implement writes artifacts/jira-implement-verify.json.
- GitHub Issue Implement writes artifacts/github-issue-implement-verify.json.
- issue-implement-work-pr includes verification_target defaulting to issue_brief and bounded remediation_max_attempts.
- PR creation and review transitions require the latest controlling FULLY_IMPLEMENTED verifier result except already-implemented no-code-change paths.
- ADDITIONAL_WORK_NEEDED remediation uses the verification report remainingWork as authoritative input.

Requirements:
- Replace or augment auto verification in issue-implement-work-pr with moonspec-verify issue-brief verification.
- Thread verification artifact paths through Jira and GitHub provider mappings.
- Block PR creation and final issue status changes on terminal verifier failures or exhausted remediation.

Owned coverage:
- DESIGN-REQ-009: STORY-005 owns issue implement flows use moonspec-verify gate.
- DESIGN-REQ-010: STORY-005 owns verification remediation loop gates transitions.
- DESIGN-REQ-012: STORY-005 owns moonspec-verify emits structured json when requested.
- DESIGN-REQ-013: STORY-005 owns verifier verdict vocabulary is workflow-consumable.
- DESIGN-REQ-015: STORY-005 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: STORY-006
Assumptions:
- Default remediation attempts remain a product decision; 2 is the recommended issue-implement default.

### STORY-006: Extend moonspec-verify for issue briefs
As a verification step running without a full MoonSpec feature directory, I need moonspec-verify to verify Jira and GitHub issue briefs, produce Markdown plus optional structured JSON, and provide bounded remediation handoff data.

Independent test: Run moonspec-verify in issue_brief and auto modes against Jira and GitHub brief artifacts without spec.md/plan.md/tasks.md and assert missing feature artifacts do not fail by themselves, missing brief artifacts produce the documented outcome, JSON writes only when requested, remainingWork is structured, and verdicts are documented.

Acceptance criteria:
- moonspec-verify supports moonspec_feature, issue_brief, and auto target modes.
- issue_brief mode requires the issue brief artifact and treats MoonSpec feature files as optional context.
- The inventory derives from issue title, body, acceptance criteria, labels, normalized brief, and assessment artifacts.
- The skill remains read-only except explicit verification artifact writing.
- The requested JSON shape is written when verification_artifact_path is supplied and Markdown is still returned.

Requirements:
- Update moonspec-verify source and regenerated bundle through the established skill path.
- Document all workflow-consumed verdicts consistently.
- Preserve skill projection contamination as diagnostic.
- Distinguish implementation gaps from verification gaps and emit concrete remainingWork.

Owned coverage:
- DESIGN-REQ-011: STORY-006 owns moonspec-verify supports issue-brief mode.
- DESIGN-REQ-012: STORY-006 owns moonspec-verify emits structured json when requested.
- DESIGN-REQ-013: STORY-006 owns verifier verdict vocabulary is workflow-consumable.
- DESIGN-REQ-010: STORY-006 owns verification remediation loop gates transitions.
- DESIGN-REQ-015: STORY-006 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: None

### STORY-007: Enforce workflow terminology and MoonSpec spelling
As a user reading preset, UI, or result copy, I need MoonMind queue entries described as workflows or workflow executions and MoonSpec spelled consistently, while valid domain-specific uses of task remain allowed.

Independent test: Run terminology guard tests over preset YAML and public UI copy, asserting prohibited MoonMind task phrases and Moon Spec spelling are absent while allowed domain phrases such as tasks.md, Generate TDD task breakdown, Temporal Task, Workflow Task, Activity Task, and Jira task issue type remain permitted.

Acceptance criteria:
- User-facing copy uses workflow or workflow execution for MoonMind queue entries.
- Public copy does not use MoonMind task terminology except explicit allowed domain terms.
- Public copy spells MoonSpec consistently.
- Workflow-native aliases such as createdWorkflowCount, workflows, workflowMappings, and dependencyCount are added where legacy task-shaped result fields still have consumers.

Requirements:
- Update affected preset titles, descriptions, UI labels, step instructions, and docs.
- Extend terminology guardrails for preset YAML and public UI copy.
- Keep valid uses of task where the domain requires it.

Owned coverage:
- DESIGN-REQ-014: STORY-007 owns terminology uses workflows and moonspec consistently.
- DESIGN-REQ-015: STORY-007 owns tests cover presets, tools, gating, remediation, and terminology.
Dependencies: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005
Assumptions:
- Temporary task-shaped compatibility aliases are acceptable only where existing consumers still depend on them.

## Coverage Matrix
- DESIGN-REQ-001: STORY-001, STORY-004
- DESIGN-REQ-002: STORY-001
- DESIGN-REQ-003: STORY-001
- DESIGN-REQ-004: STORY-002
- DESIGN-REQ-005: STORY-004
- DESIGN-REQ-006: STORY-004
- DESIGN-REQ-007: STORY-003
- DESIGN-REQ-008: STORY-003, STORY-004
- DESIGN-REQ-009: STORY-005
- DESIGN-REQ-010: STORY-002, STORY-005, STORY-006
- DESIGN-REQ-011: STORY-006
- DESIGN-REQ-012: STORY-005, STORY-006
- DESIGN-REQ-013: STORY-005, STORY-006
- DESIGN-REQ-014: STORY-002, STORY-003, STORY-004, STORY-007
- DESIGN-REQ-015: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007

## Dependencies
- STORY-002 depends on STORY-005
- STORY-004 depends on STORY-003, STORY-005
- STORY-005 depends on STORY-006
- STORY-007 depends on STORY-001, STORY-002, STORY-003, STORY-004, STORY-005

## Out Of Scope
- Creating Jira or GitHub issues during this breakdown step.
- Creating spec.md files or specs/ directories during this breakdown step.
- Implementing preset, workflow, tool, or verifier changes during this breakdown step.
- Changing existing preset slugs unless a later implementation story deliberately chooses and tests a migration.

## Coverage Gate
PASS - every major design point is owned by at least one story.

## Recommended First Story
STORY-006: Extend moonspec-verify for issue briefs. It unlocks the verification gate required by issue implementation and GitHub orchestration flows and can be validated independently before dependent preset work.

## Downstream Notes
No spec.md files or specs/ directories are created by this breakdown. TDD remains the default strategy for downstream /moonspec.plan, /moonspec.tasks, and /moonspec.implement. After implementation, /moonspec.verify should compare final behavior against the original design preserved through specify.
