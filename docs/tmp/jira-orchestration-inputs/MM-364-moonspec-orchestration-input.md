# MM-364 MoonSpec Orchestration Input

## Source

- Jira issue: MM-364
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [PrMergeAutomation] Remove dead legacy MergeAutomation workflow code
- Labels: `cleanup`, `pr-merge-automation`, `temporal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-364 from MM project
Summary: [PrMergeAutomation] Remove dead legacy MergeAutomation workflow code
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-364 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-364: [PrMergeAutomation] Remove dead legacy MergeAutomation workflow code

User Story
As a MoonMind platform maintainer, I want the dead legacy MergeAutomation workflow implementation removed so the repository has one unambiguous PR merge automation execution path.

Acceptance Criteria
- Remove the duplicate legacy `MoonMindMergeAutomationWorkflow` workflow class from `moonmind/workflows/temporal/workflows/merge_gate.py` while preserving helper functions still used by `merge_automation.py`, such as readiness classification, resolver request construction, idempotency key creation, and timeout helpers.
- Confirm the only registered `MoonMind.MergeAutomation` workflow implementation is `moonmind/workflows/temporal/workflows/merge_automation.py`.
- Update or remove tests that import or exercise the legacy `merge_gate.py` workflow class directly; tests should validate the active workflow path instead.
- Grep the repository for references to `merge_automation.create_resolver_run` and remove the legacy activity path if it is no longer reachable after the workflow cleanup, including activity catalog/runtime registration and tests.
- Keep the invariant that merge automation launches `pr-resolver` through a child `MoonMind.Run` with `publishMode=none`; do not replace it with direct merge logic.
- Update `docs/Tasks/PrMergeAutomation.md` or related implementation notes if they imply both workflow paths are active.

Implementation Notes
- The active worker registers `MoonMindMergeAutomationWorkflow` from `moonmind/workflows/temporal/workflows/merge_automation.py`.
- `moonmind/workflows/temporal/workflows/merge_gate.py` still defines another class with the same workflow name and an older path that launches resolver runs via `merge_automation.create_resolver_run`.
- Remove the duplicate workflow path to avoid confusing operator and implementation review of PR merge automation.
- Preserve helper functions in `merge_gate.py` when they remain used by the active `merge_automation.py` workflow.
- Remove the legacy `merge_automation.create_resolver_run` activity path, including activity catalog/runtime registration and tests, when grep confirms it is no longer reachable.

Verification
- Run focused unit tests for merge automation and run workflow boundary coverage.
- Run `./tools/test_unit.sh` before completion.
- Include grep evidence that only one workflow class is registered for `MoonMind.MergeAutomation` and that no dead legacy resolver-run activity path remains unless explicitly justified.

Out of Scope
- Adding a Mission Control merge automation enablement UI.
- Changing pr-resolver behavior.
- Changing the merge readiness policy semantics except where needed to remove unreachable code safely.

