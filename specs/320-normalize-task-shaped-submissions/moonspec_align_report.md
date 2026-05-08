# MoonSpec Alignment Report: Normalize Task-Shaped Submissions

**Date**: 2026-05-08
**Feature**: `specs/320-normalize-task-shaped-submissions`
**Source issue**: MM-627

## Findings And Remediation

| Finding | Resolution |
| --- | --- |
| `spec.md` retained the older `/speckit.breakdown` command name in a generated comment. | Updated the comment to `/moonspec-breakdown` without changing the preserved original input or source requirements. |
| `tasks.md` assigned implementation work to `FR-002` even though `plan.md` marks it `implemented_verified`. | Removed `FR-002` from the implementation task and left it covered by final validation. |
| `tasks.md` treated `FR-011` as a new failing-test item even though `plan.md` marks binary-ref behavior `implemented_verified`. | Changed the task to confirm existing frontend/backend evidence and record it during red-first validation. |

## Gate Recheck

- Prerequisite script: PASS with `SPECIFY_FEATURE=320-normalize-task-shaped-submissions .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks`
- Specify gate: PASS; `spec.md` contains exactly one user story and no clarification markers.
- Plan/design gate: PASS; `plan.md`, `research.md`, `data-model.md`, `contracts/task-shaped-submission-contract.md`, and `quickstart.md` exist.
- Tasks gate: PASS; `tasks.md` has 36 sequential tasks, exactly one story phase, unit and integration tests before implementation, red-first confirmation tasks, story validation, and final `/moonspec-verify`.
- Requirement status alignment: PASS; every `plan.md` status row is covered, and no `implemented_verified` row appears in the implementation task block.

## Downstream Regeneration

No downstream regeneration required. Alignment changed only terminology and task coverage references; the one-story scope, requirement inventory, design artifacts, and task ordering remain coherent.
