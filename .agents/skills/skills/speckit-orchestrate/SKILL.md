---
name: speckit-orchestrate
description: Run a full Spec Kit execution pipeline from one feature request. Use when the user wants one skill to execute speckit-specify, speckit-plan, speckit-tasks, speckit-analyze, remediation discovery, remediation edits, and speckit-implement in sequence.
---

# Spec Kit Orchestrate Skill

## Inputs

- One natural language feature request to pass to `speckit-specify`
- Optional constraints (scope limits, testing constraints, implementation boundaries)
- Optional source document contract path (for `Implement Docs/<path>.md` requests)

If no feature request is provided, ask for it once before starting.

## Orchestration Principle

- This skill is orchestration-first: it must drive `speckit-specify` -> `speckit-plan` -> `speckit-tasks` -> `speckit-analyze` -> remediation -> `speckit-implement`.
- It must not silently replace downstream skills with ad-hoc one-off workflows.
- For document-backed runtime requests, it must enforce end-to-end requirements traceability (`DOC-REQ-*`) across spec, plan, tasks, and completed implementation.

## Portability Goal

- This skill must work reliably both inside MoonMind presets and when executed directly as a standalone skill by Claude Code, Codex CLI, Gemini CLI, or similar agents.
- Do not assume that "invoke downstream skill" guarantees that all downstream artifacts were actually produced.
- Treat downstream skill invocations as instructions that must be verified with explicit artifact and quality gates before continuing.

## Resume Behavior

- Before running any stage, inspect the active feature directory and determine which artifacts already exist.
- Resume from the first incomplete stage instead of regenerating the entire feature by default.
- Do not overwrite a later-stage artifact that already exists unless:
  - the user explicitly requested regeneration, or
  - an earlier-stage remediation requires updating dependent artifacts to restore consistency.
- If multiple artifacts exist but are inconsistent (for example `tasks.md` exists but `plan.md` is missing), stop and report the inconsistency instead of guessing.

## Mandatory Artifact Gates

- `speckit-specify` is only considered complete when:
  - `spec.md` exists, and
  - the requirements checklist exists when the specify flow created one.
- `speckit-plan` is only considered complete when:
  - `plan.md` exists,
  - `research.md` exists,
  - `quickstart.md` exists,
  - `data-model.md` exists when the spec defines entities or data shape that planning must model,
  - `contracts/` exists when the feature exposes an interface or contract surface, and
  - `contracts/requirements-traceability.md` exists when `DOC-REQ-*` appears in `spec.md`.
- `speckit-tasks` is only considered complete when:
  - `tasks.md` exists, and
  - runtime scope validation passes for runtime mode.
- `speckit-analyze` is only considered complete when:
  - the latest analysis result exists in the expected report/output location for the active environment, or
  - the current run output includes the full analysis report content needed for remediation.
- `speckit-implement` is only considered complete when:
  - required implementation work is done,
  - completed tasks are marked `[X]` in `tasks.md`, and
  - runtime diff scope validation passes for runtime mode.
- If any required artifact or gate is missing, STOP and report the exact missing artifact or failed gate instead of continuing.

## Downstream Success Criteria

- When invoking `speckit-specify`, require a usable `spec.md` rather than a partial outline.
- When invoking `speckit-plan`, require the full planning artifact set, not just `plan.md`.
- When invoking `speckit-tasks`, require a concrete executable `tasks.md`, not a summary or abbreviated checklist.
- When invoking `speckit-analyze`, require actionable findings or an explicit no-issues result.
- When invoking `speckit-implement`, require both code changes and task-state updates when implementation is in scope.

## Workflow

1. Accept the user feature request as the canonical pipeline input.
2. Determine orchestration mode:
   - Default to `runtime`.
   - Only use `docs` mode when the user explicitly requests docs-only/doc-alignment work.
   - If input is `Implement Docs/<path>.md`, treat it as `runtime` intent where the doc is a requirements contract, not the implementation target.
3. Inspect the current feature state before running any stage:
   - Identify the active feature directory if one already exists for the current branch/context.
   - Detect which of these artifacts already exist: `spec.md`, `plan.md`, `research.md`, `quickstart.md`, `data-model.md`, `contracts/`, `tasks.md`, analysis report, implementation diff.
   - Build a stage status table internally:
     - `specify`: complete only if the specify artifact gate passes.
     - `plan`: complete only if the plan artifact gate passes.
     - `tasks`: complete only if the tasks artifact gate passes.
     - `analyze`: complete only if the analyze artifact gate passes.
     - `implement`: complete only if the implementation gate passes.
   - Resume from the first incomplete stage.
4. For `runtime` mode, append this scope guard when invoking downstream skills:
   - "Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
5. Run `speckit-specify` only if the specify stage is incomplete.
6. After `speckit-specify`, verify the specify artifact gate:
   - Require `spec.md`.
   - If the specify flow created a requirements checklist, require that checklist file too.
   - If the gate fails, stop and report that specification generation was incomplete.
7. Resolve any clarification blockers produced by `speckit-specify`.
8. If the request is document-backed (`Implement Docs/<path>.md`), run a spec traceability gate:
   - `spec.md` must contain `DOC-REQ-*` IDs for source requirements.
   - Every `DOC-REQ-*` must map to at least one functional requirement.
   - If this fails, run remediation and do not continue.
9. Run `speckit-plan` only if the planning stage is incomplete.
10. After `speckit-plan`, verify the planning artifact gate:
   - Require `plan.md`, `research.md`, and `quickstart.md`.
   - Require `data-model.md` when the feature includes entities, relationships, or modeled data.
   - Require `contracts/` when the feature defines an API, CLI contract, interface contract, or other external surface.
   - If `DOC-REQ-*` exists, require `contracts/requirements-traceability.md` with one row per `DOC-REQ-*` and non-empty validation strategy.
   - If any required planning artifact is missing, stop and report that planning was incomplete rather than assuming success.
11. Run `speckit-tasks` only if the tasks stage is incomplete.
12. After `speckit-tasks`, verify the tasks artifact gate:
   - Require `tasks.md`.
   - Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode <runtime|docs>`.
   - If the tasks gate fails in `runtime` mode, treat it as a CRITICAL blocker, run remediation (Prompt B), regenerate tasks, and re-run the gate. Do not continue until it passes or until missing context requires user input.
13. If `DOC-REQ-*` exists, run a task coverage gate:
   - Extract all `DOC-REQ-*` IDs from `spec.md`.
   - For each ID, require at least one implementation task and one validation task in `tasks.md`.
   - Missing coverage is CRITICAL; remediate before continuing.
14. Run `speckit-analyze` only if the analyze stage is incomplete or if remediation changed the artifacts.
15. Verify analyze completion before remediation:
   - Require a concrete analysis result, not just a claim that analysis ran.
   - If no analysis output exists, stop and report analyze-stage failure.
16. Run **Prompt A: Remediation Discovery**.
17. If Prompt A reports `Safe to Implement: NO DETERMINATION`, stop and notify the user with the missing context needed to proceed.
18. Run **Prompt B: Remediation Application**.
19. Re-run `speckit-analyze` once to verify remediation edits.
20. Re-run **Prompt A: Remediation Discovery** against the updated artifacts.
21. If the latest Prompt A result is `NO DETERMINATION`, stop and notify the user.
22. If the latest Prompt A result is `NO`, run one additional best-effort remediation cycle (`Prompt B` -> `speckit-analyze` -> `Prompt A`) and continue unless the result changes to `NO DETERMINATION`.
23. Run `speckit-implement` only if implementation work remains incomplete.
24. Invoke `speckit-implement` with checklist gate mode set to `auto-proceed` so one-pass orchestration does not pause for yes/no confirmation on incomplete checklists.
25. Confirm completed work is checked as `[X]` in `tasks.md`.
26. If `DOC-REQ-*` exists, run completion coverage gate:
   - Each `DOC-REQ-*` must appear in at least one completed (`[X]`) task.
   - Missing completion coverage is a blocking failure.
27. Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode <runtime|docs> --base-ref origin/main`.
28. If the diff scope gate fails in `runtime` mode, do not claim success; report failure as "implementation scope not satisfied" and return required corrective actions.
29. If all prior workflow stages succeeded (no `NO DETERMINATION`, no unresolved CRITICAL/HIGH blockers, and `speckit-implement` completed), stop by default with a validated implementation summary.
30. Only create a commit if the user explicitly requested commit creation or the orchestration environment explicitly requires commit output.
31. Only create a pull request if the user explicitly requested PR creation or the orchestration environment explicitly requires PR output.
32. If commit or PR creation cannot be completed (for example, missing auth/remote/repo permissions), stop and report the exact blocker plus the minimum manual commands required.
33. Return a concise report with:
   - feature path,
   - stage-by-stage completion status,
   - missing or regenerated artifacts,
   - files edited,
   - unresolved risks,
   - test status,
   - final Safe-to-Implement determination,
   - checklist gate outcome from `speckit-implement`,
   - scope validation results (tasks + diff),
   - `DOC-REQ-*` coverage status (if applicable),
   - commit/PR status when those actions were requested.

Do not require a manual approval step for remediations.
Stop only when Safe-to-Implement is `NO DETERMINATION` or when user input is required to resolve missing context.

## Prompt Overrides

If the user provides custom remediation prompts, use those instead of Prompt A and Prompt B.

## Prompt A: Remediation Discovery

Run this prompt immediately after `speckit-analyze`:

```text
You are reviewing a Spec Kit feature that has spec.md, plan.md, and tasks.md.
Objective: identify every recommended remediation required before implementation quality is acceptable.

Inputs:
- spec.md
- plan.md
- tasks.md
- latest speckit-analyze output

Instructions:
1. Produce a complete remediation list covering consistency gaps, ambiguity, missing requirements, dependency ordering, testing coverage, constitution alignment, and implementation readiness.
2. If orchestration mode is `runtime`, classify "no production runtime code tasks" as **CRITICAL**.
2b. If `DOC-REQ-*` exists, classify any missing `DOC-REQ-*` mapping across spec/plan/tasks as **CRITICAL**.
3. For each remediation item include:
   - Severity: CRITICAL | HIGH | MEDIUM | LOW
   - Artifact: spec.md | plan.md | tasks.md | docs
   - Location: section heading (or line reference when available)
   - Problem: one sentence
   - Remediation: specific edit required
   - Rationale: one sentence
4. Group output by artifact and order items by severity descending.
5. End with:
   - Safe to Implement: YES | NO | NO DETERMINATION
   - Blocking Remediations: list all CRITICAL/HIGH blockers
   - Determination Rationale: one sentence justifying the safety determination
```

## Prompt B: Remediation Application

Run this prompt after Prompt A output:

```text
Apply the remediations from the remediation list to the current feature artifacts.

Scope:
- spec.md
- plan.md
- tasks.md
- related docs only when required by a remediation item

Instructions:
1. Implement every CRITICAL and HIGH remediation.
2. Implement MEDIUM and LOW remediations unless they conflict with explicit user constraints.
3. Keep edits minimal, deterministic, and consistent across all artifacts.
4. Add or reorder tasks when needed to close dependency/test gaps.
5. For `runtime` mode, ensure tasks include production runtime file changes and validation tasks.
5b. If `DOC-REQ-*` exists, ensure each `DOC-REQ-*` is represented by implementation + validation tasks and traceability mappings.
6. Preserve section structure unless a remediation explicitly requires restructuring.
7. After editing, report:
   - files changed
   - remediations completed
   - remediations skipped (with reason)
   - residual risks
```

## Output Contract

Return:

- Feature id and branch name
- Stage-by-stage completion status
- Remediation summary
- Implementation and test summary from `speckit-implement`
- Commit hash (if created)
- PR link or creation status (if attempted)
- Remaining manual follow-ups (if any)
