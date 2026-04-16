# MM-351 MoonSpec Orchestration Input

## Source

- Jira issue: MM-351
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Evaluate merge gates with durable signal and polling waits
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-351 from MM board
Summary: Evaluate merge gates with durable signal and polling waits
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-351 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-351: Evaluate merge gates with durable signal and polling waits

User Story
As a workflow operator, I need MoonMind.MergeAutomation to wait on explicit external merge-readiness state instead of time delays so resolver attempts only run when the current PR head SHA is ready.

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 8. New Workflow Type
- 10. MoonMind.MergeAutomation Input and Output
- 11. MoonMind.MergeAutomation Lifecycle
- 12. Merge Gate Evaluation
- 19. Continue-As-New
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-004
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-025
- DESIGN-REQ-029

Story Metadata
- Story ID: STORY-002
- Dependency mode: depends on STORY-001
- Story dependencies from breakdown: STORY-001

Acceptance Criteria
- MoonMind.MergeAutomation accepts parent workflow identifiers, publishContextRef, mergeAutomationConfig, and resolverTemplate without embedding large publish payloads in history.
- The workflow uses initializing, awaiting_external, executing, finalizing, completed, failed, and canceled vocabulary as applicable.
- A gate evaluation blocks resolver launch until configured review/check/Jira requirements are complete for the current head SHA.
- A new PR head SHA invalidates prior completion and returns waiting blockers for the new head when requirements are not fresh.
- External GitHub/Jira events can signal the workflow to re-evaluate before the fallback timer fires.
- Fallback polling is bounded by configured fallbackPollSeconds and does not become a fixed-delay merge strategy.
- Continue-As-New preserves parent id, publish context ref, PR number/URL, latest head SHA, gate policy, Jira key, blockers, cycle count, resolver history, and expire-at deadline.

Requirements
- Gate readiness must be state-based and head-SHA-sensitive.
- Gate output must be deterministic and machine-readable.
- Long-lived waits must remain replay-safe and compact in workflow history.
- Expired waits must produce the expired terminal status through the output contract.

Dependencies
- STORY-001

Independent Test
Run Temporal workflow tests against MoonMind.MergeAutomation with a stub PublishContext and fake gate provider. Cover waiting blockers, signal-driven re-evaluation, fallback timer re-evaluation, Continue-As-New state preservation, and a gate-open output without starting a real resolver.

Source Design Coverage
- DESIGN-REQ-004: Covered by parent workflow identifiers and compact publishContextRef inputs.
- DESIGN-REQ-010: Covered by the MoonMind.MergeAutomation input and output contract.
- DESIGN-REQ-011: Covered by lifecycle vocabulary and terminal status handling.
- DESIGN-REQ-012: Covered by state-based gate evaluation before resolver launch.
- DESIGN-REQ-013: Covered by head-SHA-sensitive freshness checks.
- DESIGN-REQ-014: Covered by external GitHub/Jira signal-driven re-evaluation.
- DESIGN-REQ-015: Covered by bounded fallback polling.
- DESIGN-REQ-016: Covered by Continue-As-New state preservation.
- DESIGN-REQ-017: Covered by deterministic machine-readable gate output.
- DESIGN-REQ-018: Covered by expired wait terminal status behavior.
- DESIGN-REQ-025: Covered by replay-safe workflow event and lifecycle behavior.
- DESIGN-REQ-029: Covered by compact workflow history and large-payload avoidance.

Needs Clarification
- None
