# MoonSpec Align Report: Canonical Task Run List Route

**Feature**: `298-canonical-task-run-list`  
**Date**: 2026-05-05  
**Verdict**: PASS after conservative remediation

## Updated Artifacts

- `plan.md`: Aligned integration testing strategy with the generated task list by naming `tests/integration/api/test_tasks_list_visibility.py` as the focused hermetic integration coverage for mixed task/system/manifest visibility and broad compatibility URLs.
- `research.md`: Updated the test-tooling decision so integration coverage is mandatory for the story boundary, while full compose-backed integration remains tied to `integration_ci` marking or API/Temporal boundary changes.
- `quickstart.md`: Added the targeted integration test step and clarified final evidence expectations.
- `tasks.md`: Replaced the ambiguous red-first local note target with `artifacts/298-canonical-task-run-list/red-first.md` and aligned the final integration command task with the updated strategy.

## Key Decisions

- Integration coverage: chose a focused hermetic integration test file because THOR-370's core promise is a boundary guarantee that ordinary `/tasks/list` does not expose system or manifest rows when mixed execution data exists.
- Red-first evidence location: chose a gitignored artifact path so implementation evidence can be recorded without mutating the generated task checklist during execution.

## Validation

- `SPECIFY_FEATURE=298-canonical-task-run-list .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks`: PASS
- Structural alignment check: PASS
  - exactly one user story
  - no unresolved clarification markers
  - 40 sequential tasks
  - valid task checklist format
  - unit and integration tests before implementation tasks
  - red-first confirmation before implementation tasks
  - final `/speckit.verify` task present
  - all FR, SC, SCN, and DESIGN-REQ IDs from `spec.md` covered by `tasks.md`

## Remaining Risks

- No application tests were run because this step only changed MoonSpec artifacts.
