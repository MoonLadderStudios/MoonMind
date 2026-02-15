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

## Workflow

1. Accept the user feature request as the canonical pipeline input.
2. Determine orchestration mode:
   - Default to `runtime`.
   - Only use `docs` mode when the user explicitly requests docs-only/doc-alignment work.
   - If input is `Implement Docs/<path>.md`, treat it as `runtime` intent where the doc is a requirements contract, not the implementation target.
3. For `runtime` mode, append this scope guard when invoking downstream skills:
   - "Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
4. Invoke `speckit-specify`.
5. Resolve any clarification blockers produced by `speckit-specify`.
6. If the request is document-backed (`Implement Docs/<path>.md`), run a spec traceability gate:
   - `spec.md` must contain `DOC-REQ-*` IDs for source requirements.
   - Every `DOC-REQ-*` must map to at least one functional requirement.
   - If this fails, run remediation and do not continue.
7. Invoke `speckit-plan`.
8. If `DOC-REQ-*` exists, require `contracts/requirements-traceability.md` with one row per `DOC-REQ-*` and non-empty validation strategy.
9. Invoke `speckit-tasks`.
10. Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode <runtime|docs>`.
11. If step 10 fails in `runtime` mode, treat it as a CRITICAL blocker, run remediation (Prompt B), regenerate tasks, and re-run step 10. Do not continue until it passes or until missing context requires user input.
12. If `DOC-REQ-*` exists, run a task coverage gate:
   - Extract all `DOC-REQ-*` IDs from `spec.md`.
   - For each ID, require at least one implementation task and one validation task in `tasks.md`.
   - Missing coverage is CRITICAL; remediate before continuing.
13. Invoke `speckit-analyze`.
14. Run **Prompt A: Remediation Discovery**.
15. If Prompt A reports `Safe to Implement: NO DETERMINATION`, stop and notify the user with the missing context needed to proceed.
16. Run **Prompt B: Remediation Application**.
17. Re-run `speckit-analyze` once to verify remediation edits.
18. Re-run **Prompt A: Remediation Discovery** against the updated artifacts.
19. If the latest Prompt A result is `NO DETERMINATION`, stop and notify the user.
20. If the latest Prompt A result is `NO`, run one additional best-effort remediation cycle (`Prompt B` -> `speckit-analyze` -> `Prompt A`) and continue unless the result changes to `NO DETERMINATION`.
21. Invoke `speckit-implement` with checklist gate mode set to `auto-proceed` so one-pass orchestration does not pause for yes/no confirmation on incomplete checklists.
22. Confirm completed work is checked as `[X]` in `tasks.md`.
23. If `DOC-REQ-*` exists, run completion coverage gate:
   - Each `DOC-REQ-*` must appear in at least one completed (`[X]`) task.
   - Missing completion coverage is a blocking failure.
24. Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode <runtime|docs> --base-ref origin/main`.
25. If step 24 fails in `runtime` mode, do not commit/PR; report failure as "implementation scope not satisfied" and return required corrective actions.
26. If all prior workflow stages succeeded (no `NO DETERMINATION`, no unresolved CRITICAL/HIGH blockers, and `speckit-implement` completed), create a commit for the completed changes.
27. If step 26 succeeds, create a pull request from the feature branch with a concise summary of scope, remediation outcomes, and implementation/test results.
28. If commit or PR creation cannot be completed (for example, missing auth/remote/repo permissions), stop and report the exact blocker plus the minimum manual commands required.
29. Return a concise report with feature path, files edited, unresolved risks, test status, final Safe-to-Implement determination, checklist gate outcome from `speckit-implement`, scope validation results (tasks + diff), `DOC-REQ-*` coverage status (if applicable), and commit/PR status.

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
