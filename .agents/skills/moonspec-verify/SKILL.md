---
name: moonspec-verify
description: Verify a completed MoonSpec implementation against the original request, one-story `spec.md`, plan, tasks, AGENTS.md repo guidance, source-design mappings, and required tests. Use when the user asks to run or reproduce `/moonspec.verify`, perform the final read-only implementation check, audit unit and integration test evidence, classify requirement coverage, or decide whether more code or test work is needed before closing a spec.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Verify

Use this skill to perform the final MoonSpec verification workflow.

## Scope

Verify only. Do not modify source code, tests, specs, plans, tasks, docs, migrations, or configuration. Normal disposable test artifacts are acceptable only when already ignored by the project.

This skill answers:

- Does the implementation satisfy the original request or declarative design preserved in `spec.md`?
- Is the single story in `spec.md` fully implemented?
- Do unit tests and integration tests provide credible evidence?
- Which requirements, scenarios, source design mappings, or AGENTS.md principles remain partial, missing, conflicting, or unverified?
- Which stable canonical source claims are covered by implementation behavior, test evidence, artifact evidence, or a clear gap reason?
- Did verified implementation evidence contradict claims in the canonical source document, indicating doc drift that reconciliation must handle?

## Source Acceptance Matrix Verification

When `artifacts/moonspec/source-acceptance.json` or `artifacts/moonspec/acceptance-assessment.json` exists, verify every repo-verifiable source row. Do not choose `FULLY_IMPLEMENTED` unless every required repo-verifiable source row is satisfied.

## Inputs

- Treat the user's text as optional verification focus.
- Work from the active feature directory resolved by the prerequisite script unless the user provides a specific feature directory, `spec.md`, or issue-brief verification inputs.
- In MoonSpec feature-directory mode, require `spec.md`, `plan.md`, and `tasks.md`.
- In issue-brief verification mode, use the provided issue brief artifact, issue reference, acceptance criteria, and assessment artifact as the verification baseline without requiring a MoonSpec feature directory, `spec.md`, `plan.md`, or `tasks.md`.
- Read `AGENTS.md` when present for project principles, repo constraints, and test discipline.
- Use absolute paths in reports.
- Keep the verdict conservative when evidence is incomplete.

Stop if the required artifacts for the selected mode cannot be located. If `spec.md` contains multiple stories, report `NO_DETERMINATION` for MoonSpec completion and recommend splitting the design with `/moonspec.breakdown` or regenerating a one-story spec.

## Pre-Verify Hooks

Before verification, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_verify`.
2. If the YAML cannot be parsed or is invalid, skip hook checking silently.
3. Ignore hooks where `enabled` is explicitly `false`; hooks without `enabled` are enabled.
4. Do not evaluate non-empty `condition` expressions. Treat hooks with no condition, null condition, or empty condition as executable. Skip hooks with non-empty conditions.
5. For each executable hook:
   - Optional hook (`optional: true`): print:

```markdown
## Extension Hooks

**Optional Pre-Hook**: {extension}
Command: `/{command}`
Description: {description}

Prompt: {prompt}
To execute: `/{command}`
```

   - Mandatory hook (`optional: false`): print:

```markdown
## Extension Hooks

**Automatic Pre-Hook**: {extension}
Executing: `/{command}`
EXECUTE_COMMAND: {command}

Wait for the result of the hook command before proceeding to verification.
```

If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Setup

If the user provides issue-brief verification inputs, use issue-brief verification mode. Accept inputs expressed in prose or preset instructions such as:

- issue provider, for example `jira` or `github`
- issue reference, for example `MM-1063` or `owner/repo#123`
- issue brief artifact path, such as `artifacts/jira-implement-brief.json`
- assessment artifact path, such as `artifacts/jira-implement-assessment.json`

In issue-brief verification mode:

1. Read the issue brief artifact and assessment artifact.
2. Use the issue summary, description, acceptance criteria, loaded preset brief, and the assessment's unmet and partially-met requirements as the verification baseline.
3. Treat a `PARTIALLY_IMPLEMENTED` assessment as a bounded backlog: verify only the previously unmet or partially met requirements unless the issue brief explicitly requires broader validation.
4. Treat `FULLY_IMPLEMENTED` as already verified only when no implementation step made code changes after that assessment.
5. Inspect production code and tests directly; do not treat the assessment itself as proof that new work is complete.
6. Do not require `spec.md`, `plan.md`, `tasks.md`, or a standalone constitution file.

## Controlling vs Advisory Verification

Separate verification evidence into controlling and advisory classes before choosing the verdict.

Controlling evidence can block `FULLY_IMPLEMENTED`:

- direct production-code inspection for every in-scope requirement.
- unit, compile, typecheck, lint, and other repo-local hermetic checks that exercise in-scope behavior and can run with the checked-out repository plus documented local dependencies.
- hermetic integration tests when their required fixtures, services, and assets are present in the current runtime and the failure identifies a concrete in-scope implementation defect.
- explicit AGENTS.md, issue brief, or spec `MUST` requirements that are repo-verifiable in the current runtime.

Advisory evidence must be reported but must not fail verification by itself:

- integration, e2e, smoke, quickstart, map-entry, UI/browser, deployment, or external-service tests that require credentials, host services, deployed environments, proprietary or binary assets, large fixtures, game/editor map assets, simulators, or other runtime inputs not available in the checkout.
- failures whose root cause is the unavailable advisory environment, such as missing `.umap` files, absent `/Game/Maps/...` assets, unavailable services, or unsupported local tools.
- manual-only checks, production deployment checks, or provider-specific validation unavailable to the verifier.

When an advisory command is unavailable or fails for an advisory-environment reason, classify the command as `NOT RUN` when detected before execution, or as `FAIL` with a clearly marked non-blocking advisory note when discovered by running it. Record the missing asset, service, fixture, or environment condition in Test Results, Gaps, Diagnostics, or residual risk, but do not put it in Remaining Work and do not choose `ADDITIONAL_WORK_NEEDED`, `NO_DETERMINATION`, or `BLOCKED` solely for that reason.

If an integration or e2e command failure reveals a concrete in-scope implementation defect that can be fixed in the repository, classify and gate on that underlying defect, not on the suite label. If the implementation, controlling tests, source claims, AGENTS.md principles, and original request alignment all verify, `FULLY_IMPLEMENTED` is allowed even when advisory integration/e2e/map smoke evidence is missing or non-blocking.

If the user provides a specific `spec.md` or feature directory, use it and derive sibling artifacts from that directory.

Otherwise run the prerequisite script from the repository root:

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

Parse `FEATURE_DIR` and `AVAILABLE_DOCS`, then derive:

- `SPEC = FEATURE_DIR/spec.md`
- `PLAN = FEATURE_DIR/plan.md`
- `TASKS = FEATURE_DIR/tasks.md`
- optional docs from `AVAILABLE_DOCS`
- `REPO_GUIDANCE = AGENTS.md` when present

If shell arguments contain single quotes, use shell-safe escaping such as `'I'\''m Groot'`, or double quotes when possible.

## Workspace Projection Preflight

Before running full-suite verification commands, ensure the repository view is not contaminated by a MoonMind active skill projection:

```bash
test ! -L .agents/skills
test ! -L .gemini/skills
test ! -e skills_active || test -L skills_active
git status --porcelain -- .agents/skills .gemini/skills skills_active
```

If `.agents/skills` or `.gemini/skills` is an active projection symlink, repair the checkout view before running full-suite evidence. Prefer restoring the tracked repository files or using a clean reclone/worktree. If repair is not possible in the current runtime, stop with verdict `BLOCKED`, include the diagnostic `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION`, set `recoverableInCurrentRuntime: false`, set `recommendedNextAction: blocked`, and do not report `NO_DETERMINATION` merely because MoonMind's active projection masked tracked skill files.

Real repo-authored `.agents/skills` directories are valid source input and must not be deleted, moved, or treated as the active selected skill snapshot.

## Load Verification Sources

In MoonSpec feature-directory mode, read:

- `spec.md`: original request in `**Input**`, the single user story, independent test, acceptance scenarios or `SCN-*`, functional requirements `FR-*`, success criteria `SC-*`, edge cases, assumptions, key entities, and source design mappings such as `DESIGN-REQ-*` or `DOC-REQ-*`.
- `plan.md`: intended architecture, project structure, Principles Check, test commands, test tooling, integration dependencies, and constraints. Treat as context, not proof.
- `tasks.md`: expected file paths, sequencing, test commands, and process completion. Treat checked tasks as process evidence only, not implementation proof.
- `AGENTS.md` when present: project principles, repo constraints, `MUST` rules, and testing discipline.
- `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, and `checklists/` when present and relevant.
- `specs/breakdown.md` when source design coverage or cross-spec dependencies matter.
- The canonical source document named by `spec.md` `**Source Document**` when present, plus `artifacts/doc-discoveries/<feature>.json` when it exists, so source-document drift can be assessed.

Do not use copied source requirement text in `spec.md` as evidence that behavior exists.

In issue-brief verification mode, read:

- the issue brief artifact: issue key or GitHub issue ref, title, description, acceptance criteria, loaded preset brief, constraints, and source-resolution metadata when present.
- the assessment artifact: verdict, per-requirement statuses, unmet requirements, partially met requirements, blocking evidence, and summary.
- changed production and test files relevant to the issue brief and assessment gaps.
- local implementation artifacts named by the preset, such as pull request handoff artifacts or verification reports, only as process context.

Do not use the issue brief, assessment, or generated handoff text as evidence that behavior exists.

## Canonical Claim Coverage

When a canonical source document is present, report first-class Canonical Claim Coverage over stable canonical claims from that source while preserving the existing Source Document Drift section for reconciliation handoff.

Build the claim inventory from the canonical source document's stable claim headings and durable claim anchors, such as `DOC-REQ-*`, `CONTRACT-*`, `INV-*`, `NON-GOAL-*`, `QUALITY-*`, and `TEST-*`. Use temporary `DESIGN-REQ-*` values only as source-issue traceability, not as stable canonical claim IDs. Preserve source issue traceability or related coverage IDs when the canonical document provides it, but do not treat traceability prose as proof of behavior.

For each in-scope canonical claim, classify the result with separate fields for:

- Implementation status: `VERIFIED`, `PARTIAL`, `MISSING`, `CONFLICT`, or `NO_DETERMINATION`.
- Verification status: `VERIFIED`, `PARTIAL`, `MISSING`, `CONFLICT`, or `NO_DETERMINATION`.
- Drift status: `NONE`, `POSSIBLE_DOC_DRIFT`, or `DEFINITE_DOC_DRIFT`.

Each claim row must include at least one durable reference in one of these fields:

- Code evidence, such as repository file paths with line numbers or named code symbols.
- Test evidence, such as test file paths, test names, and command results.
- Artifact evidence, such as artifact refs, discovery ledger paths, diagnostics refs, or report refs.
- Gap reason, when evidence is missing, ambiguous, contradictory, or out of scope.

Classify gaps separately:

- Implementation gaps mean required behavior or wiring is absent, partial, or contradictory.
- Verification gaps mean tests, command evidence, or inspection evidence are missing or insufficient.
- Doc drift means verified behavior contradicts, supersedes, or reveals ambiguity in the canonical source document.

Doc drift alone does not block `FULLY_IMPLEMENTED` when implementation behavior and required verification are correct for the agreed story scope. In that case, set the claim's drift status and also record the structured drift in Source Document Drift for `moonspec-doc-reconcile`.

Use durable evidence references instead of pasting large source, code, test, artifact, or log content into the report.

## Verification Inventory

Build an internal inventory before inspecting code:

- One row per `FR-*`.
- One row per acceptance scenario or `SCN-*`.
- One row per observable success criterion or `SC-*`.
- One row per edge case that affects behavior.
- One row per relevant AGENTS.md principle, repo constraint, or testing-discipline item that affects implementation or verification.
- One row per in-scope `DESIGN-REQ-*` or `DOC-REQ-*`.
- One row per stable canonical source claim in scope for the story.
- In issue-brief verification mode, one row per acceptance criterion and one row per unmet or partially met assessment requirement.

For each row, track:

- Expected behavior.
- Likely production code touchpoints.
- Expected unit test evidence.
- Expected integration test evidence.
- Current status.
- Concrete evidence.
- Remaining gap.
- For canonical claims, separate implementation gaps, verification gaps, and doc drift.

## Inspect Evidence

Inspect production code before tests so behavior is verified directly.

Use repository search to find:

- Requirement terms.
- Entity names.
- DTOs, commands, endpoint paths, CLI names, UI routes, event names, or public APIs.
- Config keys, migration names, service registrations, background jobs, middleware, persistence code, and external integrations.
- Test names and fixtures tied to `FR-*`, `SCN-*`, success criteria, contracts, or source design IDs.

Evidence rules:

- Production behavior must exist for each functional requirement.
- Startup wiring, registration, configuration binding, routing, migrations, contracts, background jobs, external services, and persistence must be inspected when required behavior depends on them.
- Unit tests should cover domain rules, transformations, validation, edge cases, and failure modes.
- Integration tests should cover acceptance scenarios, workflows, contracts, persistence, external interfaces, CLI/API/UI wiring, and other system interactions.
- Comments, TODOs, dead code, unreferenced helpers, and documentation-only changes are non-evidence unless the requirement is explicitly documentation-only.
- Implementation must not add hidden scope that contradicts the original request, source design, spec, or relevant repo guidance.

## Run Verification Commands

Run commands when available and safe:

- Unit test commands from `plan.md`, `tasks.md`, quickstart, or project conventions.
- Integration test commands from `plan.md`, `tasks.md`, quickstart, or project conventions.
- Quickstart validation when executable and safe.
- Build, lint, or typecheck commands when they are part of the documented validation path or needed to resolve ambiguity.

Record exact command results as `PASS`, `FAIL`, or `NOT RUN`.

Use `NOT RUN` with an exact reason when a command requires unavailable credentials, missing services, unsafe side effects, unsupported local tools, or excessive environment setup.
Use the controlling/advisory split when interpreting results. Missing map assets, game/editor content assets, external services, credentials, or other non-hermetic integration/e2e prerequisites are advisory limitations unless the command output exposes a concrete in-scope implementation defect.

## Classify Items

Use these statuses:

- `VERIFIED`: implementation and validation evidence satisfy the item.
- `PARTIAL`: some implementation exists, but behavior, wiring, or test coverage is incomplete.
- `MISSING`: no meaningful implementation evidence exists.
- `CONFLICT`: implementation contradicts the spec, original request, source design, or relevant repo guidance.
- `NO_DETERMINATION`: evidence is too ambiguous or unavailable to make a defensible call.

Rules:

- Do not mark the feature `FULLY_IMPLEMENTED` unless every in-scope `FR-*`, relevant AGENTS.md principle, source design requirement, and acceptance-critical behavior is `VERIFIED`.
- Missing required unit tests or repo-local hermetic checks is a verification failure unless the spec clearly makes that test class irrelevant.
- Missing integration coverage for acceptance scenarios, contracts, workflows, persistence, or external boundaries is a high-severity gap only when that coverage is repo-local, hermetic, and controllable in the current runtime. Integration/e2e/map/deployment evidence that depends on unavailable assets or external runtime fixtures is advisory and non-blocking.
- Separate missing implementation from missing validation when both matter.
- Treat violated AGENTS.md `MUST` rules as blocking failures.
- Treat original request misalignment as blocking even if later tasks are complete.
- Source-document drift alone does not block `FULLY_IMPLEMENTED` when the implementation is correct per the agreed story scope; record it in the Source Document Drift section as structured input for `moonspec-doc-reconcile`. Drift becomes blocking only when it reveals the implementation itself contradicts agreed scope.
- Canonical claim doc drift alone follows the same rule: it is non-blocking when behavior and verification are correct, but implementation gaps and verification gaps remain blocking until resolved or explicitly out of scope.

## Verdict

Choose exactly one verdict:

- `FULLY_IMPLEMENTED`: implementation, unit tests, repo-local hermetic checks, source design requirements, relevant AGENTS.md principles, and original request alignment all verify.
- `ADDITIONAL_WORK_NEEDED`: concrete implementation or validation gaps remain.
- `NO_DETERMINATION`: required evidence cannot be inspected or commands cannot be run enough to reach a defensible conclusion.
- `BLOCKED`: the execution environment prevents trustworthy verification, such as `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION` from the workspace projection preflight. Include the diagnostic, whether it is recoverable in the current runtime, and the minimum repair needed.

Prefer `ADDITIONAL_WORK_NEEDED` over `NO_DETERMINATION` when a concrete missing code or test gap is visible. Use `BLOCKED` only for environment failures; it says nothing about implementation completeness.
Do not choose a blocking verdict solely because advisory integration/e2e/map/deployment validation cannot run or fails due to unavailable non-repo assets, services, credentials, or tooling. Use `FULLY_IMPLEMENTED` when all controlling evidence verifies; otherwise gate only on concrete implementation or controllable verification gaps.

When the verdict is `ADDITIONAL_WORK_NEEDED`, include a structured Remaining Work section that remediation steps can consume. Each item must identify:

- requirement or acceptance criterion
- gap type: `implementation`, `verification`, `documentation`, or `environment`
- concrete remaining work
- suggested files, commands, or evidence to inspect
- whether the gap is recoverable in the current runtime

Distinguish implementation gaps from verification gaps:

- Implementation gaps mean required behavior, wiring, persistence, contracts, UI/API behavior, or configuration is absent, partial, or contradictory.
- Verification gaps mean tests, command evidence, fixture coverage, integration evidence, or inspectable artifacts are missing or insufficient.
- Environment gaps mean current-runtime constraints prevent a controlling repo-verifiable check and should usually map to `BLOCKED` or `NO_DETERMINATION` depending on recoverability. Environment gaps for advisory integration/e2e/map/deployment evidence are non-blocking limitations, not Remaining Work.

Emit concrete `remainingWork` for every `ADDITIONAL_WORK_NEEDED` result. Each item must be bounded enough for a remediation step to act on without reinterpreting the whole report.

## Report

Return a Markdown report in the response. Do not write a file unless the user explicitly asks for one.

Use this structure:

```markdown
# MoonSpec Verification Report

**Feature**: [name or spec path]
**Spec**: [absolute path]
**Original Request Source**: spec.md `Input`
**Verdict**: FULLY_IMPLEMENTED | ADDITIONAL_WORK_NEEDED | NO_DETERMINATION | BLOCKED
**Confidence**: HIGH | MEDIUM | LOW

## Test Results

| Suite | Command | Result | Notes |
|-------|---------|--------|-------|
| Unit | [command] | PASS/FAIL/NOT RUN | [notes] |
| Integration | [command] | PASS/FAIL/NOT RUN | [notes] |

## Requirement Coverage

| Requirement | Evidence | Status | Notes |
|-------------|----------|--------|-------|
| FR-001 | [file/test/reference] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | [notes] |

## Acceptance Scenario Coverage

| Scenario | Evidence | Status | Notes |
|----------|----------|--------|-------|

## Story Scope, Principles, And Source Claim Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DOC-REQ-001 / AGENTS.md principle | [file/test/reference] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | [notes] |

## Canonical Claim Coverage

| Claim | Code Evidence | Test Evidence | Artifact Evidence | Implementation Status | Verification Status | Drift Status | Gap Reason |
|-------|---------------|---------------|-------------------|-----------------------|---------------------|--------------|------------|
| DOC-REQ-001 | [file:line or symbol] | [test path/name or command] | [artifact/ref] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | NONE/POSSIBLE_DOC_DRIFT/DEFINITE_DOC_DRIFT | [clear reason or empty when fully evidenced] |

## Original Request Alignment

- [Pass/fail summary against the verbatim original request]

## Source Document Drift

| Doc Claim | Observed Behavior | Evidence | Severity |
|-----------|-------------------|----------|----------|
| [docs/ path + claim] | [verified behavior] | [file/test/reference] | definite/possible |

## Gaps

- [Blocking gaps first]

## Remaining Work

- [Ordered, concrete code or test changes required before completion]

For issue-brief verification mode, set:

- **Feature**: issue reference
- **Spec**: N/A (issue-brief verification mode)
- **Original Request Source**: issue brief artifact path

Use this structured form for each Remaining Work item:

```markdown
- Requirement: [criterion or assessment requirement]
  Gap Type: implementation | verification | documentation | environment
  Remaining Work: [bounded work]
  Suggested Evidence: [files, tests, commands, or artifact refs]
  Recoverable In Current Runtime: true | false
```

## Decision

- [Final recommendation and smallest credible next step if not complete]
```

Keep the report evidence-backed and concise. Cite file paths and line numbers when possible.

## Post-Verify Hooks

After reporting, check `.specify/extensions.yml` for `hooks.after_verify` using the same parsing, filtering, and condition rules as pre-verify hooks. For each executable hook:

- Optional hook (`optional: true`): print:

```markdown
## Extension Hooks

**Optional Hook**: {extension}
Command: `/{command}`
Description: {description}

Prompt: {prompt}
To execute: `/{command}`
```

- Mandatory hook (`optional: false`): print:

```markdown
## Extension Hooks

**Automatic Hook**: {extension}
Executing: `/{command}`
EXECUTE_COMMAND: {command}
```

If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Key Rules

- Verification is read-only except ignored disposable test artifacts.
- `spec.md` defines the bounded one-story verification scope and preserves the original request/source packet.
- When `spec.md` names a canonical source document, interpret the story against that document's in-scope stable claims; the canonical document remains the durable desired-state authority unless verified drift is handed off to doc reconciliation.
- Do not require unrelated claims from a larger canonical design to verify for this story, but do not let the temporary spec silently override an in-scope canonical conflict.
- Relevant AGENTS.md guidance defines repo principles, constraints, and test discipline for the story.
- `plan.md` and `tasks.md` are useful context but never proof of implementation.
- Unit tests and repo-local hermetic checks are controlling expected evidence. Integration/e2e/map/deployment checks are expected evidence when available, but they are advisory and non-blocking when they depend on unavailable non-repo assets, services, credentials, or tooling.
- Prefer direct, citeable repository evidence from production code, wiring, configuration, and tests.
- Do not mark the feature complete when required behavior is only inferred and not verified.
