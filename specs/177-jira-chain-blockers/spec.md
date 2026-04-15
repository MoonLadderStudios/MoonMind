# Feature Specification: Jira Chain Blockers

**Feature Branch**: `177-jira-chain-blockers`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-339 from TOOL board
Summary: Jira Chain Blockers
Issue type: Story
Current Jira status at fetch time: Selected for Development

Use this Jira preset brief as the canonical Moon Spec orchestration input. Preserve the Jira issue key MM-339 and TOOL board reference in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-339: Jira Chain Blockers

Summary: Add ordered blocker-chain support to Jira Breakdown story creation
Description:
Update MoonMind so the Jira Breakdown flow can create multiple Jira stories from a technical design and optionally link them in execution order. In the initial scope, when MoonMind creates stories 1, 2, 3..., it should create Jira dependency links so story 2 is blocked by story 1, story 3 is blocked by story 2, and so on. Keep this within MoonMind's trusted Jira boundary rather than relying on prompt text alone or manual post-processing. MoonMind's Jira tool surface is already designed as a narrow trusted server-side integration, so this feature should extend that same model rather than introducing raw Jira mutation from agent shells.
Objective:
Allow the Jira Breakdown preset and related Jira story-creation paths to produce an ordered Jira issue chain from a declarative design, with explicit blocker links created automatically after issue creation succeeds.
Requirements:
Add preset/runtime support for a Jira dependency mode, with at least: none, linear_blocker_chain. Update the Jira Breakdown preset so it can request ordered dependency-link creation in addition to issue creation. The preset currently invokes moonspec-breakdown followed by jira-issue-creator; this behavior should be extended rather than replaced. Preserve and consume ordered story output from MoonSpec breakdown, including stable story IDs and dependencies. MoonSpec breakdown already requires ordered stories and per-story dependencies; reuse that contract rather than inventing a separate ordering format. Extend MoonMind's trusted Jira backend to support Jira issue-link creation so blocker links are created by backend code, not inferred as documentation-only output. Update the Jira issue creation path so it creates all target Jira issues, maps story order/IDs to created Jira issue keys, creates Jira dependency links for the selected mode, and returns both created issues and created links in the result. Keep the agent-skill path (jira-issue-creator) and the deterministic structured-output path (story.create_jira_issues) aligned on the same ordered-linking contract. MoonMind already documents both paths, so this feature should not create a third divergent path. Preserve current fallback behavior when Jira export cannot complete fully: partial issue creation must be reported honestly, with link failures surfaced explicitly rather than reported as full success. Current story export already has partial-success fallback semantics; extend those to dependency-link creation.

Acceptance Criteria:
- Given a design that breaks down into three ordered stories, when Jira Breakdown runs with dependency mode linear_blocker_chain, then MoonMind creates three Jira issues and creates two blocker links so issue 2 is blocked by issue 1 and issue 3 is blocked by issue 2.
- Given Jira Breakdown runs with dependency mode none, when the stories are created, then MoonMind creates the Jira issues without creating dependency links.
- Given ordered story creation succeeds but one Jira link creation fails, when the run completes, then MoonMind returns a partial-success result that includes created issue keys, identifies the failed link operation, and does not claim the dependency chain is complete.
- Given a rerun or retry occurs after uncertain Jira create/link state, when MoonMind attempts the export, then it avoids silently duplicating issues or links and reports any reused existing issue/link state clearly.
- Given the Jira target configuration is missing or invalid, when the Jira Breakdown export path runs, then MoonMind keeps the existing fallback behavior and surfaces the story breakdown handoff path instead of claiming Jira success.
- Given the feature is used through the preset or a structured story-output tool path, when the export runs, then both paths honor the same dependency-mode contract and produce equivalent ordered-link behavior.

Implementation Notes:
- Touch the Jira Breakdown preset, MoonSpec breakdown skill, Jira issue creator skill, structured story-output tool path, and trusted Jira integration surfaces.
- Add validation for linear blocker-chain success, no-link mode, partial link failure, retry/idempotency behavior, and preset/runtime-planner propagation of dependency mode.

Out of Scope:
- Arbitrary non-linear dependency graphs.
- Jira board/sprint placement changes.
- Automatic MoonMind task scheduling from Jira dependency state.
- Browser-side Jira mutation logic.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Ordered Jira Story Dependency Chain

**Summary**: As a MoonMind operator exporting a Jira Breakdown result, I want MoonMind to create Jira issues and optionally link them in ordered blocker-chain mode so that generated implementation stories carry explicit Jira dependencies without manual post-processing.

**Goal**: The Jira Breakdown and related Jira story-creation paths can create an ordered set of Jira stories, apply the selected dependency mode, and report created issues and dependency links honestly through MoonMind's trusted Jira boundary.

**Independent Test**: Run the Jira Breakdown export path and the structured story-output path with ordered three-story input in both `linear_blocker_chain` and `none` modes, then verify the created issue keys, dependency-link results, partial-failure reporting, and retry/idempotency behavior match the selected mode.

**Acceptance Scenarios**:

1. **Given** a design breaks down into three ordered stories, **When** Jira Breakdown runs with dependency mode `linear_blocker_chain`, **Then** MoonMind creates three Jira issues and two blocker links so story 2 is blocked by story 1 and story 3 is blocked by story 2.
2. **Given** a design breaks down into ordered stories, **When** Jira Breakdown runs with dependency mode `none`, **Then** MoonMind creates the Jira issues without creating dependency links.
3. **Given** issue creation succeeds and one dependency link fails, **When** the export completes, **Then** MoonMind returns a partial-success result that includes created issue keys, identifies the failed link operation, and does not claim the dependency chain is complete.
4. **Given** a rerun or retry occurs after uncertain Jira create or link state, **When** MoonMind attempts the export again, **Then** it avoids silently duplicating issues or links and reports reused existing issue or link state clearly.
5. **Given** Jira target configuration is missing or invalid, **When** the export path runs, **Then** MoonMind keeps the existing fallback behavior and surfaces the story breakdown handoff path instead of claiming Jira success.
6. **Given** the feature is used through the preset-driven agent-skill path or the structured story-output path, **When** either path exports the same ordered stories with the same dependency mode, **Then** both paths honor the same contract and produce equivalent issue and link behavior.

### Edge Cases

- The ordered story output contains fewer than two stories, so `linear_blocker_chain` has no links to create but still reports the selected mode honestly.
- A story dependency references an unknown story ID or an order that cannot be resolved.
- Jira reports an existing issue or link during retry after a previous uncertain attempt.
- Jira allows issue creation but denies issue-link creation.
- Link creation partially succeeds after all issues are created.
- Dependency mode is missing, blank, or unsupported.
- Provider errors include details that must not be exposed as raw credentials or unsanitized failure text.

## Assumptions

- The active story is Jira issue MM-339 from the TOOL board.
- The supported dependency modes for this story are `none` and `linear_blocker_chain`.
- A linear blocker chain is derived from the ordered story output: each later story is blocked by the immediately preceding story.
- Existing partial-success and fallback semantics for Jira issue export remain the baseline for new dependency-link failures.
- The feature does not require browser-side Jira mutation or changes to board placement.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept a Jira story dependency mode for Jira Breakdown and related Jira story-creation exports, with supported values `none` and `linear_blocker_chain`.
- **FR-002**: System MUST preserve ordered story output, stable story identifiers, and declared dependencies from MoonSpec breakdown through Jira issue creation.
- **FR-003**: System MUST create Jira issues for all target stories before creating dependency links for the selected mode.
- **FR-004**: When dependency mode is `linear_blocker_chain`, system MUST create blocker links so each story after the first is blocked by its immediately preceding ordered story.
- **FR-005**: When dependency mode is `none`, system MUST create Jira issues without creating dependency links and MUST report that no links were requested.
- **FR-006**: System MUST create dependency links through MoonMind's trusted server-side Jira integration boundary, not through raw Jira mutation from agent shells or prompt-only instructions.
- **FR-007**: Jira export results MUST include created or reused issue keys and created, reused, skipped, or failed dependency-link outcomes.
- **FR-008**: Partial dependency-link failure MUST produce an honest partial-success result that identifies failed link operations without claiming the chain is complete.
- **FR-009**: Retry or rerun behavior MUST avoid silently duplicating Jira issues or dependency links and MUST report any reused existing state.
- **FR-010**: Missing or invalid Jira target configuration MUST preserve the existing fallback behavior by surfacing the story breakdown handoff path instead of reporting Jira success.
- **FR-011**: The preset-driven `jira-issue-creator` path and deterministic `story.create_jira_issues` path MUST honor the same dependency-mode contract and produce equivalent ordered-link behavior for the same inputs.
- **FR-012**: Unsupported, blank, or malformed dependency modes MUST fail fast with a validation result and MUST NOT silently fall back to another dependency mode.
- **FR-013**: Jira provider failures surfaced to operators MUST be sanitized and MUST preserve actionable context without exposing raw credentials or secret-like values.

### Key Entities

- **Jira Dependency Mode**: The selected export behavior for story links. Supported values are `none` and `linear_blocker_chain`.
- **Ordered Story Output**: The breakdown result containing stable story identifiers, story order, and dependency metadata used to create Jira issues and links.
- **Created Jira Issue Mapping**: A mapping from ordered story identifiers to Jira issue keys, including whether each issue was newly created or reused.
- **Jira Dependency Link Result**: The outcome for one requested Jira link, including source issue key, target issue key, link type, status, and failure details when applicable.
- **Jira Export Result**: The aggregate export outcome containing issue mappings, dependency mode, link results, fallback details, and partial-success status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation with a three-story ordered input in `linear_blocker_chain` mode creates exactly three issue mappings and exactly two blocker-link success or reuse results.
- **SC-002**: Validation with the same ordered input in `none` mode creates exactly three issue mappings and zero requested link operations.
- **SC-003**: Validation of one link failure after successful issue creation reports partial success, includes all created issue keys, identifies the failed link, and does not mark the dependency chain complete.
- **SC-004**: Retry validation after uncertain issue or link state reports reused existing issue or link state and does not create duplicate issues or duplicate links.
- **SC-005**: Preset-driven and structured story-output exports with the same ordered input and dependency mode produce equivalent dependency-mode, issue-mapping, and link-result fields.
- **SC-006**: Final verification can trace Jira issue MM-339 from TOOL board to the implemented behavior, validation evidence, and pull request metadata.
