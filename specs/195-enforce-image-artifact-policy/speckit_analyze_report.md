# MoonSpec Alignment Report: Enforce Image Artifact Storage and Policy

**Date**: 2026-04-17  
**Feature**: `specs/195-enforce-image-artifact-policy`  
**Mode**: Automated conservative alignment after task generation

## Findings

| Finding | Severity | Remediation |
| --- | --- | --- |
| `spec.md` and `verification.md` state that unsupported runtimes fail explicitly for image attachments, but `tasks.md` only named unsupported future fields for the FR-009/SC-005 unit-test task. | Medium | Updated T006 to name unsupported target runtime coverage and added a focused unit regression for a task submission that combines `inputAttachments` with an unsupported `targetRuntime`. |

## Gate Re-Check

- Specify gate: PASS. The active spec remains a single-story runtime feature and preserves the original MM-368 Jira preset brief.
- Plan gate: PASS. Required planning artifacts remain present and unchanged: `plan.md`, `research.md`, `data-model.md`, `contracts/image-attachment-policy.md`, and `quickstart.md`.
- Task gate: PASS. `tasks.md` covers exactly one story, red-first unit tests, integration/contract tests, implementation tasks, story validation, and final `/moonspec-verify` work.
- Constitution gate: PASS. No new dependencies, storage, compatibility aliases, or architecture exceptions were introduced.

## Validation

- `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_rejects_unsupported_runtime_with_attachments -q`: PASS.
- `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py -q`: PASS, 80 passed and 12 warnings.
- `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks`: PASS.
- `git diff --check`: PASS.
