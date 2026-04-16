# MoonSpec Alignment Report: Merge Automation Waits

MoonSpec alignment was run after task generation for `specs/186-merge-automation-waits`.

## Findings

| Finding | Severity | Resolution |
| --- | --- | --- |
| The repository already had an older MM-341 merge-gate spec and implementation using `MoonMind.MergeGate`, while MM-351 and the canonical source design require `MoonMind.MergeAutomation`. | High | Kept MM-351 as a separate feature directory, updated runtime contracts to the canonical workflow type, and refreshed operator-facing docs and historical 179 artifacts so canonical references no longer point at `MoonMind.MergeGate`. |
| The generated tasks used existing test file names containing `merge_gate`, while the canonical runtime contract is `MoonMind.MergeAutomation`. | Low | Kept existing test/module file names as internal implementation locations to minimize churn, but changed workflow type names, activity names, models, assertions, and docs to the canonical contract. |
| The official MoonSpec prerequisite helper expects a branch name like `001-feature-name`, but this managed run branch is `mm-351-4489ffb5`. | Low | Continued with the active `.specify/feature.json` and direct artifact inspection; recorded the script blocker in verification evidence. |

## Validation

- `specs/186-merge-automation-waits/spec.md` contains exactly one user story and preserves MM-351 in `**Input**`.
- `plan.md`, `research.md`, `data-model.md`, `contracts/merge-automation-contract.md`, `quickstart.md`, and `tasks.md` exist.
- Tasks include test-first coverage for FR-001 through FR-013, acceptance scenarios 1-8, and in-scope DESIGN-REQ mappings.
- No unresolved clarification markers remain.
