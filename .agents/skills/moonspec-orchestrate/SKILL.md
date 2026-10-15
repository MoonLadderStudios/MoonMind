---
name: moonspec-orchestrate
description: Orchestrate the full MoonSpec lifecycle from a preselected single-story feature request or active feature directory through specification, planning, TDD task generation, artifact alignment, implementation, and final verification. Use when the user asks for an end-to-end MoonSpec run and the input has already been routed to one independently testable story, or when Codex needs to coordinate `moonspec-specify`, `moonspec-plan`, `moonspec-tasks`, `moonspec-align`, `moonspec-implement`, and `moonspec-verify` without manual analyze/remediation prompts.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Orchestrate

Use this skill to coordinate the MoonSpec workflow end to end.

## Scope

Orchestrate downstream MoonSpec skills instead of reimplementing their detailed workflows:

- `moonspec-specify`: create one-story specs from a single feature request.
- `moonspec-assess`: assess source acceptance evidence into a bounded backlog.
- `moonspec-plan`: create implementation planning artifacts.
- `moonspec-tasks`: create a TDD-first executable task breakdown.
- `moonspec-align`: analyze and remediate artifact drift before implementation.
- `moonspec-implement`: implement the task breakdown with TDD.
- `moonspec-verify`: perform final read-only verification.

MoonSpec skill IDs and command IDs use `moonspec-*` and `/moonspec.*`.

## Inputs

- Required: a preselected single-story feature request or active feature directory.
- Optional: implementation constraints, testing constraints, scope boundaries, source design paths, target story, or verification focus.
- If no request or active feature context is available, ask once for the missing input before starting.
- Inputs that still need story splitting are out of scope for this skill. Route them through a higher-level workflow such as `moonspec-breakdown` before starting MoonSpec Orchestrate.

Default intent is `runtime`: satisfy requested behavior, reusing existing implementation where appropriate. Use `docs` intent only when the user explicitly asks for documentation-only work.

## Core Rules

- MoonSpec uses one independently testable story per `spec.md`.
- Before starting downstream stages, validate that the input is already exactly one independently testable story or an active feature directory with an existing one-story `spec.md`.
- If the input is a broad design, names multiple stories/features, asks to split or implement all stories, or otherwise cannot be bounded to one independently testable story without selection, stop immediately and report that a higher-level workflow must route it through `moonspec-breakdown` or another upstream selector first.
- Single-story requests go through `moonspec-specify`.
- Run `moonspec-assess` after `moonspec-specify` when source acceptance evidence exists. Preserve the original source acceptance matrix, the bounded backlog, and any note that a single story may span multiple implementation surfaces.
- TDD is the default strategy.
- Apply requirement statuses before all coverage and execution gates: `unverified` rows retain source IDs, reason and verifiableBy without executable tasks; `needs_human` rows retain the exact decision, owner, and action without executing it or claiming its completion. Only executable rows require code, tests, setup, and red-first evidence. Observed defects remain executable remediation; unavailable checks do not become defects.
- The original request or source design preserved in `spec.md` `**Input**` is the final alignment source. Derived artifacts never silently drop or override requirements it adopted.
- `moonspec-align` replaces the old multi-step analyze remediation sequence.
- Do not synthesize user approvals, scripted user responses, or pretend an analyze report exists.
- Resume from existing artifacts when they pass gates; do not regenerate by default.
- Preserve unrelated user changes in the worktree.

## Resume Model

Before running a stage, inspect the active feature state:

- `spec.md`
- `plan.md`
- `research.md`
- `data-model.md`
- `contracts/`
- `quickstart.md`
- `tasks.md`
- relevant production code and tests
- previous verification report when available in the conversation

Resume from the first incomplete stage. If artifacts are inconsistent, use `moonspec-align` when `spec.md`, `plan.md`, and `tasks.md` exist; otherwise stop and report the missing artifact that prevents safe alignment.

Do not overwrite later-stage artifacts unless:

- the user requested regeneration, or
- `moonspec-align` determines conservative artifact edits are required, or
- an upstream artifact changed and downstream artifacts must be regenerated to remain coherent.

## Artifact Gates

Use these gates before advancing:

- Specify gate:
  - `spec.md` exists.
  - It contains exactly one user story.
  - The original request or source design is preserved in `**Input**`.
  - Requirements are testable and contain no unresolved story-critical clarification.
- Plan gate:
  - `plan.md` exists.
  - `research.md` and `quickstart.md` exist.
  - `data-model.md` exists when the story involves data.
  - `contracts/` exists when the story exposes an API, CLI, UI contract, event, parser, library surface, or external interface.
  - Unit and integration strategies are identified for executable rows; disclosure-only or authority-held rows use N/A.
- Tasks gate:
  - `tasks.md` exists.
  - It covers exactly one story.
  - Executable rows have applicable test, red-first, implementation, and validation tasks; all-Unverified stories need only disclosures and final `/moonspec.verify`.
  - Source mappings have executable task coverage, Unverified disclosures, or explicit `needs_human` handoffs.
- Align gate:
  - `moonspec-align` has run after `tasks.md` generation unless the user explicitly skipped alignment.
  - Any artifact changes from alignment have been validated or propagated.
- Implement gate:
  - Required implementation work is done.
  - Completed tasks are marked `[X]`.
  - Feasible planned tests have results; unavailable checks retain Unverified disclosures. No test or code work is required for an all-Unverified story.
- Verify gate:
  - `moonspec-verify` produced a concrete report for the active `spec.md`.
  - Verdict is not `NO_DETERMINATION` or `BLOCKED`.
  - Completion is claimed only when verdict is `FULLY_IMPLEMENTED` and no actual `needs_human` authority handoff for the selected operation remains.

If a gate fails, stop or run the appropriate upstream skill. Do not continue on a claimed success without artifacts or evidence.

## Workflow

### 1. Create Or Select Spec

Use the provided input as a preselected single-story request or active feature directory:

1. Validate the single-story precondition before running `moonspec-specify`.
2. Stop immediately if the input still needs story splitting or upstream story selection.
3. Run `moonspec-specify` if `spec.md` does not already exist.
4. Verify the specify gate.
5. Do not run `moonspec-breakdown` or create more than one spec from this workflow.

### 2. Assess Matching Source Acceptance

Run `moonspec-assess` before planning when `artifacts/moonspec/source-acceptance.json`
exists and its `featureId` matches the active feature. Preserve the assessment's
bounded backlog, Unverified disclosures, and actual authority handoffs for planning.
If no matching source matrix exists, skip assessment without blocking planning.
A new or changed assessment makes existing plan and tasks stale when source,
statuses, backlog, disclosures, or authority handoffs change. Refresh the plan
before regenerating tasks, even if their artifact gates previously passed.
An unchanged assessment or timestamp-only rewrite does not require regeneration.

### 3. Plan

For the selected spec:

1. Run `moonspec-plan` if the plan gate is incomplete or its assessment changed.
2. Verify generated artifacts.
3. Stop if planning requires unresolved user input.

### 4. Generate Tasks

1. Run `moonspec-tasks` if the tasks gate is incomplete or upstream artifacts changed.
2. Verify task format and source traceability, applying TDD and unit/integration coverage only to executable rows; preserve disclosures, authority handoffs, and final `/moonspec.verify`.

### 5. Align Artifacts

1. Run `moonspec-align` after task generation.
2. Let `moonspec-align` make conservative artifact edits without asking for follow-up responses.
3. If `moonspec-align` changes `spec.md`, `plan.md`, design artifacts, or `tasks.md`, re-check downstream gates.
4. Regenerate only the downstream artifact that is now stale; do not restart the full pipeline automatically.

This replaces the old manual analyze remediation flow. Do not provide scripted "yes", "continue", or other user responses to get through analyze limitations.

### 6. Implement

1. Run `moonspec-implement` for authorized executable work; skip implementation when no executable work remains. Preserve actual authority handoffs without attempting their actions.
2. Require applicable tests and red-first confirmation before new production code, never for disclosure-only rows.
3. Require completed work to be marked `[X]` in `tasks.md`.
4. Preserve story scope; do not add hidden scope to make verification easier.

### 7. Verify

1. Run `moonspec-verify` as the final gate unless an up-to-date verification report already covers the current code and artifacts.
2. If verdict is `FULLY_IMPLEMENTED`, report success.
3. If verdict is `ADDITIONAL_WORK_NEEDED`, follow the verifier's `recommendedNextAction`. Run automatic remediation only for `reattempt_current_step`; `needs_human` and `blocked` stop with the report's concrete authority, decision, or environment reason. When reattempting, use the report as the remaining-work list:
   - run `moonspec-implement` for bounded code, test, or required in-scope documentation gaps,
   - rerun targeted unit/integration validation for executable changes; review documentation-only corrections,
   - rerun `moonspec-verify`.
4. Run at most two verification-remediation cycles unless the user explicitly asks to keep going.
5. If verdict is `NO_DETERMINATION`, stop and report the exact missing evidence, commands, or context.
6. If verdict is `BLOCKED`, stop and report the environment diagnostic and minimum repair; do not count it as a remediation cycle.

## Multi-Spec Designs

Multi-spec orchestration is out of scope for this skill. A higher-level workflow must split, order, and select stories before invoking MoonSpec Orchestrate for each individual story.

## Commit And PR Behavior

Do not create commits or pull requests unless the user explicitly asks or the current orchestration environment requires that output.

If commit or PR creation is requested but cannot complete because of missing authentication, remote configuration, branch state, or permissions, report the exact blocker and the minimum manual command needed.

## Final Report

Return a concise report:

```markdown
## MoonSpec Orchestration

Feature:
- [active spec path]

Stages:
- Specify: PASS/FAIL/SKIPPED
- Plan: PASS/FAIL/SKIPPED
- Tasks: PASS/FAIL/SKIPPED
- Align: PASS/FAIL/SKIPPED
- Implement: PASS/FAIL/SKIPPED
- Verify: FULLY_IMPLEMENTED/ADDITIONAL_WORK_NEEDED/NO_DETERMINATION/BLOCKED/SKIPPED

Changed files:
- [paths]

Tests:
- [command]: [PASS/FAIL/NOT RUN]

Remaining risks:
- [risk or "None"]

Next action:
- [only if work remains]
```

Mention source design coverage and `DOC-REQ-*`/`DESIGN-REQ-*` status when present.

## Key Rules

- Use `moonspec-*` skills for downstream work.
- Use `moonspec-align`, not the old analyze prompt workaround.
- Do not invent user approvals or fake intermediate outputs.
- Do not run `moonspec-breakdown` or perform story routing from inside this workflow.
- Enforce one story per spec.
- Apply TDD and testing to executable work; keep final `/moonspec.verify` for evidence, disclosures, and actual authority handoffs.
- Treat verification as the final authority for completion.
- Never let derived artifacts silently override the original request.
