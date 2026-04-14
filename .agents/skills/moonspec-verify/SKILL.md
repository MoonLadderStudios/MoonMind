---
name: moonspec-verify
description: Verify a completed Moon Spec implementation against the original request, one-story `spec.md`, plan, tasks, constitution, source-design mappings, and required tests. Use when the user asks to run or reproduce `/speckit.verify`, perform the final read-only implementation check, audit unit and integration test evidence, classify requirement coverage, or decide whether more code or test work is needed before closing a spec.
---

# MoonSpec Verify

Use this skill to perform the final Moon Spec verification workflow.

## Scope

Verify only. Do not modify source code, tests, specs, plans, tasks, docs, migrations, or configuration. Normal disposable test artifacts are acceptable only when already ignored by the project.

This skill answers:

- Does the implementation satisfy the original request or declarative design preserved in `spec.md`?
- Is the single story in `spec.md` fully implemented?
- Do unit tests and integration tests provide credible evidence?
- Which requirements, scenarios, source design mappings, or constitution rules remain partial, missing, conflicting, or unverified?

## Inputs

- Treat the user's text as optional verification focus.
- Work from the active feature directory resolved by the prerequisite script unless the user provides a specific feature directory or `spec.md`.
- Require `spec.md`, `plan.md`, `tasks.md`, and `.specify/memory/constitution.md`.
- Use absolute paths in reports.
- Keep the verdict conservative when evidence is incomplete.

Stop if the required artifacts cannot be located. If `spec.md` contains multiple stories, report `NO_DETERMINATION` for Moon Spec completion and recommend splitting the design with `/speckit.breakdown` or regenerating a one-story spec.

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

If the user provides a specific `spec.md` or feature directory, use it and derive sibling artifacts from that directory.

Otherwise run the prerequisite script from the repository root:

```bash
scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

On PowerShell projects, use:

```powershell
scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks
```

Parse `FEATURE_DIR` and `AVAILABLE_DOCS`, then derive:

- `SPEC = FEATURE_DIR/spec.md`
- `PLAN = FEATURE_DIR/plan.md`
- `TASKS = FEATURE_DIR/tasks.md`
- optional docs from `AVAILABLE_DOCS`
- `CONSTITUTION = .specify/memory/constitution.md`

If shell arguments contain single quotes, use shell-safe escaping such as `'I'\''m Groot'`, or double quotes when possible.

## Load Verification Sources

Read:

- `spec.md`: original request in `**Input**`, the single user story, independent test, acceptance scenarios or `SCN-*`, functional requirements `FR-*`, success criteria `SC-*`, edge cases, assumptions, key entities, and source design mappings such as `DESIGN-REQ-*` or `DOC-REQ-*`.
- `plan.md`: intended architecture, project structure, test commands, test tooling, integration dependencies, and constraints. Treat as context, not proof.
- `tasks.md`: expected file paths, sequencing, test commands, and process completion. Treat checked tasks as process evidence only, not implementation proof.
- `.specify/memory/constitution.md`: `MUST` constraints and quality gates.
- `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, and `checklists/` when present and relevant.
- `specs/breakdown.md` when source design coverage or cross-spec dependencies matter.

Do not use copied source requirement text in `spec.md` as evidence that behavior exists.

## Verification Inventory

Build an internal inventory before inspecting code:

- One row per `FR-*`.
- One row per acceptance scenario or `SCN-*`.
- One row per observable success criterion or `SC-*`.
- One row per edge case that affects behavior.
- One row per constitution constraint or `CC-*`.
- One row per in-scope `DESIGN-REQ-*` or `DOC-REQ-*`.

For each row, track:

- Expected behavior.
- Likely production code touchpoints.
- Expected unit test evidence.
- Expected integration test evidence.
- Current status.
- Concrete evidence.
- Remaining gap.

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
- Implementation must not add hidden scope that contradicts the original request, source design, spec, or constitution.

## Run Verification Commands

Run commands when available and safe:

- Unit test commands from `plan.md`, `tasks.md`, quickstart, or project conventions.
- Integration test commands from `plan.md`, `tasks.md`, quickstart, or project conventions.
- Quickstart validation when executable and safe.
- Build, lint, or typecheck commands when they are part of the documented validation path or needed to resolve ambiguity.

Record exact command results as `PASS`, `FAIL`, or `NOT RUN`.

Use `NOT RUN` with an exact reason when a command requires unavailable credentials, missing services, unsafe side effects, unsupported local tools, or excessive environment setup.

## Classify Items

Use these statuses:

- `VERIFIED`: implementation and validation evidence satisfy the item.
- `PARTIAL`: some implementation exists, but behavior, wiring, or test coverage is incomplete.
- `MISSING`: no meaningful implementation evidence exists.
- `CONFLICT`: implementation contradicts the spec, original request, source design, or constitution.
- `NO_DETERMINATION`: evidence is too ambiguous or unavailable to make a defensible call.

Rules:

- Do not mark the feature `FULLY_IMPLEMENTED` unless every in-scope `FR-*`, constitution constraint, source design requirement, and acceptance-critical behavior is `VERIFIED`.
- Missing required unit or integration tests is a verification failure unless the spec clearly makes that test class irrelevant.
- Missing integration coverage for acceptance scenarios, contracts, workflows, persistence, or external boundaries is a high-severity gap.
- Separate missing implementation from missing validation when both matter.
- Treat violated constitution `MUST` rules as blocking failures.
- Treat original request misalignment as blocking even if later tasks are complete.

## Verdict

Choose exactly one verdict:

- `FULLY_IMPLEMENTED`: implementation, unit tests, integration tests, source design requirements, constitution constraints, and original request alignment all verify.
- `ADDITIONAL_WORK_NEEDED`: concrete implementation or validation gaps remain.
- `NO_DETERMINATION`: required evidence cannot be inspected or commands cannot be run enough to reach a defensible conclusion.

Prefer `ADDITIONAL_WORK_NEEDED` over `NO_DETERMINATION` when a concrete missing code or test gap is visible.

## Report

Return a Markdown report in the response. Do not write a file unless the user explicitly asks for one.

Use this structure:

```markdown
# MoonSpec Verification Report

**Feature**: [name or spec path]
**Spec**: [absolute path]
**Original Request Source**: spec.md `Input`
**Verdict**: FULLY_IMPLEMENTED | ADDITIONAL_WORK_NEEDED | NO_DETERMINATION
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

## Constitution And Source Design Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DESIGN-REQ-001 / CC-001 | [file/test/reference] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | [notes] |

## Original Request Alignment

- [Pass/fail summary against the verbatim original request]

## Gaps

- [Blocking gaps first]

## Remaining Work

- [Ordered, concrete code or test changes required before completion]

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
- The original request in `spec.md` is the source of truth for final alignment.
- Moon Spec uses one story per spec.
- `spec.md` plus `.specify/memory/constitution.md` define governing requirements.
- `plan.md` and `tasks.md` are useful context but never proof of implementation.
- Unit tests and integration tests are both expected evidence.
- Production code, wiring, configuration, migrations, contracts, and tests are stronger evidence than task checkboxes.
- Do not mark the feature complete when behavior is only inferred.
- Report concrete remaining code or test work when verification fails.
