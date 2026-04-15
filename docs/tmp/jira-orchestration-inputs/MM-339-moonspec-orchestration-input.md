# MM-339 MoonSpec Orchestration Input

## Source

- Jira issue: MM-339
- Board scope: TOOL
- Issue type: Story
- Current status at fetch time: Selected for Development
- Summary: Jira Chain Blockers
- Canonical source: `recommendedImports.presetInstructions` from the normalized Jira issue detail response

## Canonical MoonSpec Feature Request

MM-339: Jira Chain Blockers

Summary: Add ordered blocker-chain support to Jira Breakdown story creation
Description:
Update MoonMind so the Jira Breakdown flow can create multiple Jira stories from a technical design and optionally link them in execution order. In the initial scope, when MoonMind creates stories 1, 2, 3..., it should create Jira dependency links so story 2 is blocked by story 1, story 3 is blocked by story 2, and so on. Keep this within MoonMind's trusted Jira boundary rather than relying on prompt text alone or manual post-processing. MoonMind's Jira tool surface is already designed as a narrow trusted server-side integration, so this feature should extend that same model rather than introducing raw Jira mutation from agent shells.
Objective:
Allow the Jira Breakdown preset and related Jira story-creation paths to produce an ordered Jira issue chain from a declarative design, with explicit blocker links created automatically after issue creation succeeds.
Requirements:
Add preset/runtime support for a Jira dependency mode, with at least: none, linear_blocker_chain. Update the Jira Breakdown preset so it can request ordered dependency-link creation in addition to issue creation. The preset currently invokes moonspec-breakdown followed by jira-issue-creator; this behavior should be extended rather than replaced. Preserve and consume ordered story output from MoonSpec breakdown, including stable story IDs and dependencies. MoonSpec breakdown already requires ordered stories and per-story dependencies; reuse that contract rather than inventing a separate ordering format. Extend MoonMind's trusted Jira backend to support Jira issue-link creation so blocker links are created by backend code, not inferred as documentation-only output. Update the Jira issue creation path so it creates all target Jira issues, maps story order/IDs to created Jira issue keys, creates Jira dependency links for the selected mode, and returns both created issues and created links in the result. Keep the agent-skill path (jira-issue-creator) and the deterministic structured-output path (story.create_jira_issues) aligned on the same ordered-linking contract. MoonMind already documents both paths, so this feature should not create a third divergent path. Preserve current fallback behavior when Jira export cannot complete fully: partial issue creation must be reported honestly, with link failures surfaced explicitly rather than reported as full success. Current story export already has partial-success fallback semantics; extend those to dependency-link creation.

## Supplemental Acceptance Criteria

- Given a design that breaks down into three ordered stories, when Jira Breakdown runs with dependency mode `linear_blocker_chain`, then MoonMind creates three Jira issues and creates two blocker links so issue 2 is blocked by issue 1 and issue 3 is blocked by issue 2.
- Given Jira Breakdown runs with dependency mode `none`, when the stories are created, then MoonMind creates the Jira issues without creating dependency links.
- Given ordered story creation succeeds but one Jira link creation fails, when the run completes, then MoonMind returns a partial-success result that includes created issue keys, identifies the failed link operation, and does not claim the dependency chain is complete.
- Given a rerun or retry occurs after uncertain Jira create/link state, when MoonMind attempts the export, then it avoids silently duplicating issues or links and reports any reused existing issue/link state clearly.
- Given the Jira target configuration is missing or invalid, when the Jira Breakdown export path runs, then MoonMind keeps the existing fallback behavior and surfaces the story breakdown handoff path instead of claiming Jira success.
- Given the feature is used through the preset or a structured story-output tool path, when the export runs, then both paths honor the same dependency-mode contract and produce equivalent ordered-link behavior.

## Implementation Notes

Touch these surfaces:

- `api_service/data/task_step_templates/jira-breakdown.yaml`
- `.agents/skills/moonspec-breakdown/SKILL.md`
- `.agents/skills/jira-issue-creator/SKILL.md`
- `moonmind/workflows/temporal/story_output_tools.py`
- Jira integration models/client/tool surfaces under `moonmind/integrations/jira/`
- Focused tests under `tests/unit/workflows/temporal/` and Jira integration tests

Verification:

- Add or extend unit tests for linear blocker-chain success.
- Add or extend unit tests for no-link mode.
- Add or extend unit tests for partial link failure after issue creation.
- Add or extend unit tests for retry/idempotency behavior.
- Add or extend unit tests for preset/runtime-planner propagation of dependency mode.
- Run focused validation for the story-output tool tests and Jira integration tests.
- Existing tests already cover issue creation, subtask creation, existing-issue reuse, and partial fallback; extend that suite with dependency-link cases.

Out of scope:

- Arbitrary non-linear dependency graphs.
- Jira board/sprint placement changes.
- Automatic MoonMind task scheduling from Jira dependency state.
- Browser-side Jira mutation logic.
