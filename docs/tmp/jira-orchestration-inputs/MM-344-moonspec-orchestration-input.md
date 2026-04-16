# MM-344 MoonSpec Orchestration Input

## Source

- Jira issue: MM-344
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-003: Normalize Claude decision and hook provenance
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-344 from MM board
Summary: MoonSpec STORY-003: Normalize Claude decision and hook provenance
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-344 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-344: MoonSpec STORY-003: Normalize Claude decision and hook provenance

User Story
As a security reviewer, I need every Claude tool, file, network, MCP, hook, classifier, and prompt gate normalized as a DecisionPoint with provenance so approval behavior is explainable after the run.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.4 Model deterministic and non-deterministic safety controls explicitly
- 12. Decision pipeline
- 18.5 Work events
- 18.6 Decision events
- 21.5 Hook governance

Coverage IDs
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-025
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-003
- Short name: claude-decision-pipeline
- Dependency mode: none
- Story dependencies from breakdown: STORY-001, STORY-002

Acceptance Criteria
- Decision stages execute in the documented order from session_state_guard through checkpoint_capture.
- Deny, ask, and allow rule precedence is deterministic with first matching rule behavior.
- Protected paths are never silently auto-approved and are recorded with origin_stage = protected_path.
- Classifier outcomes are distinguishable from user approvals and policy outcomes.
- Headless unresolved decisions deny or defer according to policy and hook output.
- Hook executions emit source scope, event type, matcher, outcome, and audit data.

Requirements
- Broaden DecisionPoint beyond simple approval prompts.
- Record provenance for policy, hook, sandbox, classifier, user, and runtime resolutions.
- Emit normalized work and decision events for each stage that materially affects execution.
- Ensure hooks may tighten restrictions but cannot override matching deny or ask policy rules.

Independent Test
Submit representative tool proposals through the decision pipeline with pretool hooks, deny/ask/allow rules, protected paths, sandbox substitution, auto classifier outcomes, interactive prompts, and posttool hooks, then assert emitted DecisionPoint and HookAudit records.

Out of Scope
- Policy source resolution.
- Checkpoint storage payloads.
- Team messaging.

Source Design Coverage
- DESIGN-REQ-011: Owns normalization of Claude tool, file, network, MCP, classifier, and prompt-gate decisions as provenance-bearing DecisionPoint records.
- DESIGN-REQ-012: Owns deterministic deny, ask, and allow rule precedence across the decision pipeline.
- DESIGN-REQ-025: Owns normalized work and decision events for stages that materially affect execution.
- DESIGN-REQ-028: Owns hook governance, including pretool and posttool hook audit data and restrictions that cannot override matching deny or ask policy rules.

Needs Clarification
- None

Notes
This story depends on the Claude managed-session core schema and session launch contract stories so DecisionPoint and HookAudit provenance can attach to the shared session-plane records and runtime boundary.
