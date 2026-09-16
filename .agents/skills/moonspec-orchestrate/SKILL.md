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
- `moonspec-doc-reconcile`: update the owning canonical document when verified discoveries show it is impossible, unclear, or inconsistent, or escalate instead of editing.

MoonSpec skill IDs and command IDs use `moonspec-*` and `/moonspec.*`.

## Inputs

- Required: a preselected single-story feature request or active feature directory.
- Optional: implementation constraints, testing constraints, scope boundaries, source design paths, target story, or verification focus.
- If no request or active feature context is available, ask once for the missing input before starting.
- Inputs that still need story splitting are out of scope for this skill. Route them through a higher-level workflow such as `moonspec-breakdown` before starting MoonSpec Orchestrate.

Default intent is `runtime`: production code plus tests must be delivered. Use `docs` intent only when the user explicitly asks for documentation-only work.

## Core Rules

- MoonSpec uses one independently testable story per `spec.md`.
- Before starting downstream stages, validate that the input is already exactly one independently testable story or an active feature directory with an existing one-story `spec.md`.
- If the input is a broad design, names multiple stories/features, asks to split or implement all stories, or otherwise cannot be bounded to one independently testable story without selection, stop immediately and report that a higher-level workflow must route it through `moonspec-breakdown` or another upstream selector first.
- Single-story requests go through `moonspec-specify`.
- Run `moonspec-assess` after `moonspec-specify` when source acceptance evidence exists. Preserve the original source acceptance matrix, the bounded backlog, and any note that a single story may span multiple implementation surfaces.
- TDD is the default strategy.
- Unit tests and integration tests are both expected.
- The original request or source design preserved in `spec.md` `**Input**` is the final alignment source, interpreted against the canonical source document per `docs/Workflows/MoonSpecDocumentModel.md`. When a derived artifact conflicts with its canonical source document, the canonical document wins unless verified evidence shows the document itself is impossible, unclear, or inconsistent — then the conflict goes to doc reconciliation, never silent override.
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
  - Unit and integration test strategies are identified.
- Tasks gate:
  - `tasks.md` exists.
  - It covers exactly one story.
  - It includes unit test tasks, integration test tasks, red-first confirmation tasks, implementation tasks, story validation, and final `/moonspec.verify`.
  - Source design or original request mappings have task coverage.
- Align gate:
  - `moonspec-align` has run after `tasks.md` generation unless the user explicitly skipped alignment.
  - Any artifact changes from alignment have been validated or propagated.
- Implement gate:
  - Required implementation work is done.
  - Completed tasks are marked `[X]`.
  - Unit and integration tests have been run or blocked with exact reasons.
- Verify gate:
  - `moonspec-verify` produced a concrete report for the active `spec.md`.
  - Verdict is not `NO_DETERMINATION` or `BLOCKED`.
  - Completion is claimed only when verdict is `FULLY_IMPLEMENTED`.
- Doc Reconcile gate:
  - `moonspec-doc-reconcile` has run after a `FULLY_IMPLEMENTED` verdict when at least one canonical source candidate exists: `spec.md` records a canonical source document, the breakdown `sourceReference.path` points under `docs/`, or the orchestration step provides a source design path under `docs/`.
  - An escalate-only invocation triggered by documentation-type verification gaps is also valid; it must produce `escalated` or `no_update_required` without document edits.
  - Its result is exactly one of `updated`, `no_update_required`, or `escalated`, with rationale.

If a gate fails, stop or run the appropriate upstream skill. Do not continue on a claimed success without artifacts or evidence.

## Workflow

### 1. Create Or Select Spec

Use the provided input as a preselected single-story request or active feature directory:

1. Validate the single-story precondition before running `moonspec-specify`.
2. Stop immediately if the input still needs story splitting or upstream story selection.
3. Run `moonspec-specify` if `spec.md` does not already exist.
4. Verify the specify gate.
5. Do not run `moonspec-breakdown` or create more than one spec from this workflow.

### 2. Plan

For the selected spec:

1. Run `moonspec-plan` if the plan gate is incomplete.
2. Verify generated artifacts.
3. Stop if planning requires unresolved user input.

### 3. Generate Tasks

1. Run `moonspec-tasks` if the tasks gate is incomplete or upstream artifacts changed.
2. Verify `tasks.md` format, single-story focus, TDD order, unit/integration coverage, source traceability, and final `/moonspec.verify`.

### 4. Align Artifacts

1. Run `moonspec-align` after task generation.
2. Let `moonspec-align` make conservative artifact edits without asking for follow-up responses.
3. If `moonspec-align` changes `spec.md`, `plan.md`, design artifacts, or `tasks.md`, re-check downstream gates.
4. Regenerate only the downstream artifact that is now stale; do not restart the full pipeline automatically.

This replaces the old manual analyze remediation flow. Do not provide scripted "yes", "continue", or other user responses to get through analyze limitations.

### 5. Implement

1. Run `moonspec-implement` when implementation work remains.
2. Require TDD execution: unit tests and integration tests are written and confirmed failing before production code.
3. Require completed work to be marked `[X]` in `tasks.md`.
4. Preserve story scope; do not add hidden scope to make verification easier.

### 6. Verify

1. Run `moonspec-verify` as the final gate unless an up-to-date verification report already covers the current code and artifacts.
2. If verdict is `FULLY_IMPLEMENTED`, report success.
3. If verdict is `ADDITIONAL_WORK_NEEDED`, use the verification report as the remaining-work list:
   - run `moonspec-implement` for bounded code or test gaps,
   - rerun targeted unit/integration validation,
   - rerun `moonspec-verify`.
4. If the remaining work is dominated by `documentation`-type gaps — the canonical document, not the code, blocks completion, for example because it demands something that cannot be satisfied as written — do not spend remediation cycles on it: run `moonspec-doc-reconcile` in escalate-only mode so the document problem becomes a tracked escalation, then stop and report that outcome.
5. Run at most two verification-remediation cycles unless the user explicitly asks to keep going.
6. If verdict is `NO_DETERMINATION`, stop and report the exact missing evidence, commands, or context.
7. If verdict is `BLOCKED`, stop and report the environment diagnostic and minimum repair; do not count it as a remediation cycle.

### 7. Reconcile Declarative Docs

Run only when the final verdict is `FULLY_IMPLEMENTED` and at least one canonical source candidate exists: `spec.md` records a canonical source document, the breakdown `sourceReference.path` points under `docs/`, or the orchestration step provides a source design path under `docs/`. (The escalate-only invocation from the Verify stage is the one exception; it never edits documents.)

1. Run `moonspec-doc-reconcile` with the canonical document path(s), the latest verification report (including its Source Document Drift section), and `artifacts/doc-discoveries/<feature>.json` when present.
2. Accept exactly one outcome: `updated` (canonical doc edited), `no_update_required` (gate not met), or `escalated` (a tracker issue when integration exists, otherwise a full escalation record in the structured output and report).
3. `escalated` does not retroactively fail verification; report the issue key or escalation record alongside the outcome.
4. Skip this stage with outcome `no_update_required` when no canonical source candidate exists.

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
- Doc Reconcile: updated/no_update_required/escalated/SKIPPED

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
- Keep TDD, unit tests, integration tests, and `/moonspec.verify` in the pipeline.
- Treat verification as the final authority for completion.
- Run doc reconciliation after a `FULLY_IMPLEMENTED` verdict when a canonical source candidate exists; use escalate-only mode when documentation-type gaps block verification. Never let derived artifacts silently override canonical docs.
