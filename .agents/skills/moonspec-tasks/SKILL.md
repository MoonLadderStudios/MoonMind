---
name: moonspec-tasks
description: Generate a one-story, TDD-first Moon Spec `tasks.md` from `spec.md`, `plan.md`, and design artifacts. Use when the user asks to run or reproduce `/speckit.tasks`, create or update an executable task breakdown, map one Moon Spec story to implementation tasks, require unit and integration tests before code, preserve source-design traceability, or include final `/speckit.verify` work.
---

# MoonSpec Tasks

Use this skill to generate an executable `tasks.md` for one Moon Spec story.

## Scope

Generate task artifacts only. Do not implement application code, run the implementation, create issues, or split broad designs. Use `moonspec-breakdown` for broad technical or declarative designs, `moonspec-specify` for creating a one-story spec, and `moonspec-plan` for planning artifacts.

The generated task list must:

- Cover exactly one independently testable story from `spec.md`.
- Follow TDD by default.
- Include unit tests and integration tests before production implementation.
- Include red-first confirmation tasks.
- Preserve traceability to the original request or source design preserved in `spec.md`.
- Include final `/speckit.verify` work after implementation and tests pass.

## Inputs

- Treat the user's text as optional task-generation guidance.
- Work from the active feature directory resolved by the prerequisite script.
- Require `spec.md` and `plan.md`.
- Use `templates/tasks-template.md` as the output structure.
- Use absolute paths in reports.

Stop if `spec.md` is missing, `plan.md` is missing, or the spec contains more than one user story. Tell the user to run `/speckit.breakdown` for broad designs or `/speckit.specify` for a one-story replacement.

## Pre-Tasks Hooks

Before generating tasks, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_tasks`.
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

Wait for the result of the hook command before proceeding to task generation.
```

If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Setup

Run the prerequisite script from the repository root:

```bash
scripts/bash/check-prerequisites.sh --json
```

On PowerShell projects, use:

```powershell
scripts/powershell/check-prerequisites.ps1 -Json
```

Parse `FEATURE_DIR` and `AVAILABLE_DOCS`, then derive:

- `SPEC = FEATURE_DIR/spec.md`
- `PLAN = FEATURE_DIR/plan.md`
- `TASKS = FEATURE_DIR/tasks.md`
- optional docs from `AVAILABLE_DOCS`
- `CONSTITUTION = .specify/memory/constitution.md`

If shell arguments contain single quotes, use shell-safe escaping such as `'I'\''m Groot'`, or double quotes when possible.

## Load Context

Read:

- `plan.md`: tech stack, libraries, project structure, unit test tooling, integration test tooling, constraints, validation commands, and any `## Requirement Status` table.
- `spec.md`: preserved `**Input**`, single user story, goal, independent test, acceptance scenarios, edge cases, functional requirements, success criteria, assumptions, and source design mappings such as `DESIGN-REQ-*` or `DOC-REQ-*`.
- `.specify/memory/constitution.md`: project constraints and test discipline.
- `data-model.md` when present: entities, relationships, validation rules, and state transitions.
- `contracts/` when present: public interfaces and contract or integration test obligations.
- `research.md` when present: technical decisions that affect setup or implementation tasks.
- `quickstart.md` when present: validation commands and end-to-end scenarios.
- `specs/breakdown.md` when present: source design coverage and cross-spec dependency context.

Build a traceability inventory before writing tasks:

- One row per `FR-*`.
- One row per acceptance scenario or `SCN-*`.
- One row per meaningful edge case.
- One row per measurable success criterion or `SC-*`.
- One row per in-scope `DESIGN-REQ-*` or `DOC-REQ-*`.
- One row per constitution requirement that affects implementation or testing.

For each row, carry forward the matching `## Requirement Status` entry from `plan.md` when present. Allowed statuses are `missing`, `partial`, `implemented_unverified`, and `implemented_verified`; if a row has no status, treat it as `missing`.

Status handling:

- `missing`: generate failing unit and/or integration test tasks, red-first confirmation tasks, and implementation tasks.
- `partial`: generate failing tests for the missing behavior, red-first confirmation tasks, and implementation tasks that complete the requirement.
- `implemented_unverified`: generate verification tests first, red-first confirmation tasks when the expected behavior is not already covered, and explicit fallback implementation tasks to run if verification fails.
- `implemented_verified`: do not generate new implementation tasks by default, but preserve traceability and include final validation so the existing evidence remains checked.

Each inventory row needs task coverage appropriate to its status and either unit or integration test coverage unless it is `implemented_verified` with strong existing evidence. Acceptance scenarios, external boundaries, workflows, persistence, contracts, CLI/API/UI behavior, and service interactions require integration test coverage unless the spec clearly makes integration irrelevant or the row is already `implemented_verified` by existing integration evidence.

## Generate tasks.md

Create or replace `FEATURE_DIR/tasks.md` using `templates/tasks-template.md`. Preserve the template's structure, but replace all sample tasks with concrete tasks for the current story.

Fill:

- Feature name.
- Input paths.
- Prerequisites.
- Unit test command.
- Integration test command.
- Source traceability summary.
- Story summary.
- Independent test.
- Story traceability IDs.
- Unit test plan.
- Integration test plan.
- Task phases.
- Dependencies and execution order.
- Parallel examples.
- Implementation strategy.

When `plan.md` includes `## Requirement Status`, reflect those statuses in the source traceability summary and implementation strategy. The task list must distinguish code-and-test work, verification-only work, contingency implementation work, and already-verified rows.

The output must be immediately executable by an LLM without extra context.

## Task Format

Every generated task must use this exact checklist shape:

```text
- [ ] [TaskID] [P?] Description with file path
```

Rules:

- Start every task with `- [ ]`.
- Use sequential task IDs: `T001`, `T002`, `T003`, and so on.
- Use `[P]` only for tasks that can run in parallel because they touch different files and do not depend on incomplete tasks.
- Include exact file paths.
- Include requirement, scenario, or source IDs when the task implements or validates behavior.
- Do not include story labels such as `[US1]`; this task list covers one story.

Good examples:

```markdown
- [ ] T001 Create project structure in src/ and tests/ per implementation plan
- [ ] T012 [P] Add failing unit test for validation FR-001 in tests/unit/test_user.py
- [ ] T013 [P] Add failing integration test for signup scenario SCN-001 in tests/integration/test_signup.py
- [ ] T014 Implement UserService for FR-001 in src/services/user_service.py
```

Bad examples:

```markdown
- [ ] Create User model
T001 Create model
- [ ] [US1] Create User model
- [ ] T010 Write tests later if needed
```

## Task Organization

Generate these phases.

### Phase 1: Setup

Include project initialization and tool configuration:

- Shared project structure.
- Dependency or package configuration.
- Linting and formatting.
- Unit test tooling.
- Integration test tooling.

### Phase 2: Foundational

Include only blocking prerequisites for the story:

- Shared infrastructure the story truly depends on.
- Base schema, migrations, fixtures, or seed data.
- Authentication, routing, middleware, configuration, logging, or error handling when required.
- Integration-only runtime dependencies such as Docker, service containers, external emulators, test fixtures, or contract harnesses.

No story implementation work can begin until this phase is complete.

### Phase 3: Story

Create exactly one story phase. Include:

- Story summary.
- Independent test.
- Traceability IDs.
- Unit test plan.
- Integration test plan.
- Unit test tasks.
- Integration test tasks.
- Red-first confirmation tasks.
- Implementation tasks.
- Story validation task.

Order story work as:

1. Unit tests.
2. Integration tests.
3. Red-first confirmation.
4. Fallback implementation tasks for `implemented_unverified` rows, explicitly conditional on verification failure.
5. Models or entities.
6. Services or domain logic.
7. Endpoints, UI, CLI, public API, or contracts.
8. Integration wiring.
9. Story validation.

Unit and integration tests must be written and confirmed failing before implementation tasks for `missing` and `partial` rows. For `implemented_unverified` rows, verification tests should be run before fallback implementation tasks; if they pass, the fallback implementation tasks are skipped and final verification preserves traceability.

### Final Phase: Polish And Verification

Include only work that strengthens the completed story without adding hidden scope:

- Refactoring.
- Documentation.
- Performance tuning.
- Security hardening.
- Additional edge-case coverage.
- Additional operational integration coverage.
- Quickstart validation.
- A final task to run `/speckit.verify` after implementation and tests pass.

## Coverage Rules

- Each `FR-*` with status `missing` or `partial` must map to at least one implementation task and at least one unit or integration test task.
- Each `FR-*` with status `implemented_unverified` must map to at least one verification test task and one conditional fallback implementation task.
- Each `FR-*` with status `implemented_verified` must map to existing evidence and a final validation task; do not add implementation work unless the user asks for stronger coverage.
- Each acceptance scenario must map to at least one integration test task unless it is `implemented_verified` by existing integration evidence.
- Each source design workflow or public boundary must map to at least one integration test task unless it is `implemented_verified` by existing integration evidence.
- Each source design rule, invariant, edge case, or failure mode must map to at least one unit test task where applicable unless it is `implemented_verified` by existing unit or integration evidence.
- Each explicit non-goal or constraint must map to a guardrail test, validation task, or documented scope check when it affects implementation.
- Each contract must map to a contract or integration test before implementation.
- Each task that validates behavior must name the file path and requirement/scenario/source IDs.

Do not generate tasks for future stories, broad refactors, speculative infrastructure, or implementation layers that are not needed by the single story.

## Parallelization Rules

Mark a task `[P]` only when:

- It touches different files from other parallel tasks.
- It has no dependency on incomplete work.
- Its output is not required by another task in the same parallel group.

Safe parallel candidates often include:

- Independent setup config files.
- Different unit test files.
- Different integration test files.
- Independent models.
- Independent documentation or polish tasks.

Do not mark tasks `[P]` when they modify the same file, share generated artifacts, require ordered red-first confirmation, or depend on a common uncompleted fixture.

## Validation

Before reporting, verify:

- `tasks.md` exists in `FEATURE_DIR`.
- Every task follows `- [ ] T### [P?] Description with file path`.
- Task IDs are sequential.
- Exactly one story phase exists.
- No P1/P2/P3 or multi-story phases exist.
- Unit test tasks precede implementation tasks.
- Integration test tasks precede implementation tasks.
- Red-first confirmation tasks exist before production code tasks.
- Every traceability inventory row has task coverage.
- `## Requirement Status` rows from `plan.md`, when present, are reflected without forcing unnecessary implementation tasks for `implemented_verified` rows.
- `implemented_unverified` rows include fallback implementation tasks that are conditional on verification failure.
- Final `/speckit.verify` task exists.

If validation fails, update `tasks.md` and validate again before reporting.

## Report

Report:

- `tasks.md` path.
- Total task count.
- Task count per phase.
- Parallel opportunities.
- Independent test criteria.
- Source design or original request coverage summary.
- Requirement status coverage summary, including code-and-test, verification-only, conditional fallback, and already-verified counts when `plan.md` provides statuses.
- Required unit and integration test coverage.
- Unit and integration test tooling.
- Final verification task status.
- Confirmation that the task list covers exactly one story.
- Format validation result.

## Post-Tasks Hooks

After reporting, check `.specify/extensions.yml` for `hooks.after_tasks` using the same parsing, filtering, and condition rules as pre-tasks hooks. For each executable hook:

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

- Generate tasks for one story only.
- Keep TDD as the default strategy.
- Unit tests and integration tests are required.
- Red-first confirmation tasks are required before production code.
- Preserve traceability to `spec.md` `**Input**` and source design mappings.
- Consume `plan.md` `## Requirement Status` when present so tasks match planned code, verification-only, conditional fallback, or already-verified work.
- Use concrete file paths and requirement/scenario/source IDs.
- Include final `/speckit.verify`.
- Do not implement code from this skill.
