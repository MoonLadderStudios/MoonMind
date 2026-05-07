# MoonSpec Alignment Report: Normalize Proposal Intent in Temporal Submissions

**Created**: 2026-05-06
**Feature**: `specs/309-normalize-proposal-intent`
**Source**: MM-595 canonical Jira preset brief preserved in `spec.md`

## Findings

| Area | Result | Evidence | Action |
| --- | --- | --- | --- |
| Source preservation | PASS | `spec.md` preserves MM-595, original preset brief, and DESIGN-REQ-003 through DESIGN-REQ-006 | No change |
| Story scope | PASS | `spec.md` contains one user story: Canonical Proposal Intent | No change |
| Plan/design coverage | PASS | `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-intent-normalization.md`, and `quickstart.md` exist and map the same proposal-intent contract | No change |
| Test-first ordering | PASS | `tasks.md` orders unit tests, integration tests, red-first confirmation, implementation, story validation, and final `/speckit.verify` | No change |
| Task executability | FIXED | T016 and T021 named "appropriate" or later-discovered files instead of exact target paths | Updated T016 and T021 to name concrete service/API paths and require a new explicit task if scheduler discovery finds another writer |

## Key Decisions

- Scheduler/promotion uncertainty: chose not to invent a scheduler file path. T004 remains the mapping task, while T016/T021 now name the known proposal-promotion and API paths and require a new explicit task if another scheduler proposal-intent writer is found. This preserves exact-file task executability without hiding discovery work.
- No spec or plan scope change was needed because the source and plan already require cross-surface proof for API, promotion, Codex managed-session task creation, and any scheduler writer found during implementation.

## Gate Recheck

- Specify gate: PASS - one story, no unresolved clarification markers, original MM-595 preset brief preserved.
- Plan gate: PASS - `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` exist with explicit unit and integration strategies.
- Tasks gate: PASS - `tasks.md` covers one story, includes unit and integration tests before implementation, red-first confirmation, implementation tasks, story validation, and final `/speckit.verify`.

## Remaining Risks

- The concrete scheduler writer, if any, is intentionally left to T004 discovery. The task list now requires adding a new explicit task before editing any discovered scheduler file.
