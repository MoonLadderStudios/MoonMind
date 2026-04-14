---
name: moonspec-implement
description: Implement a single-story Moon Spec task breakdown with test-driven development. Use when the user asks to run or reproduce `/speckit.implement`, execute `tasks.md`, build the story from a Moon Spec plan, write failing unit and integration tests before production code, mark tasks complete, or prepare the implementation for `/speckit.verify`.
---

# MoonSpec Implement

Use this skill to perform the Moon Spec implementation workflow.

## When To Use

Use this skill after `/speckit.tasks` has produced a complete `tasks.md` for one Moon Spec story.

Good triggers include:

- Run or reproduce `/speckit.implement`.
- Execute the task breakdown in `tasks.md`.
- Build the current Moon Spec story from `spec.md` and `plan.md`.
- Apply TDD to a Moon Spec task list.
- Add required unit and integration test evidence before final verification.

Do not use this skill to split a broad design, write a spec, create a plan, or generate tasks. Use `moonspec-breakdown`, `moonspec-specify`, `moonspec-plan`, or `/speckit.tasks` for those steps.

## Inputs

- Treat the user's text as optional implementation guidance.
- Work from the active feature directory resolved by the prerequisite script.
- Require `spec.md`, `plan.md`, and `tasks.md`.
- The spec must contain exactly one independently testable user story.
- `tasks.md` must be specific enough to execute without inventing hidden product scope.
- Use absolute paths in reports and cite files changed or validated.

If `tasks.md` is missing or incomplete, stop and recommend `/speckit.tasks`. If the source input is a broad technical or declarative design that has not been split into one-story specs, stop and recommend `/speckit.breakdown`.

## Pre-Implement Hooks

Before implementation, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_implement`.
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

Wait for the result of the hook command before proceeding to implementation.
```

If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Setup

Run the prerequisite script from the repository root:

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

## Checklist Gate

If `FEATURE_DIR/checklists/` exists, scan every checklist file:

- Total items: lines matching `- [ ]`, `- [X]`, or `- [x]`.
- Completed items: lines matching `- [X]` or `- [x]`.
- Incomplete items: lines matching `- [ ]`.

Report a table:

```text
| Checklist | Total | Completed | Incomplete | Status |
|-----------|-------|-----------|------------|--------|
| requirements.md | 12 | 12 | 0 | PASS |
```

If any checklist has incomplete items, stop and ask whether to proceed. Continue only when the user explicitly says yes, proceed, or continue.

## Load Context

Read enough context to implement without guessing:

- `tasks.md`: task IDs, phases, dependencies, parallel markers, file paths, required test commands, and final verification task.
- `plan.md`: tech stack, architecture, project structure, test tooling, integration dependencies, and constraints.
- `spec.md`: preserved `**Input**`, the single story, independent test, acceptance scenarios, edge cases, functional requirements, success criteria, assumptions, and source design mappings.
- `.specify/memory/constitution.md`: project constraints, quality gates, and TDD expectations.
- `data-model.md` when present: entities, relationships, validation rules, and state transitions.
- `contracts/` when present: public interfaces and contract or integration test obligations.
- `research.md` when present: technical decisions and constraints.
- `quickstart.md` when present: executable integration scenarios and validation commands.
- `specs/breakdown.md` when present: source design coverage and cross-spec dependency context for specs generated by `/speckit.breakdown`.

Validate before editing code:

- `spec.md` has exactly one independently testable user story.
- Any `DESIGN-REQ-*` or `DOC-REQ-*` mappings are traceable to planned code or test work.
- `tasks.md` includes unit test tasks and integration test tasks unless the spec clearly makes one class irrelevant.
- Implementation tasks do not introduce scope outside the story, original request, or source design constraints.

If the spec contains multiple stories, stop and tell the user to split the design with `/speckit.breakdown` or regenerate a one-story spec with `/speckit.specify`.

## Project Setup Verification

Before story work, create or verify ignore files based on the actual repository:

- If `git rev-parse --git-dir` succeeds, ensure `.gitignore` exists and covers generated outputs, dependencies, local env files, logs, caches, and test artifacts.
- If Dockerfiles or Docker usage are present, create or verify `.dockerignore`.
- If ESLint legacy config exists, create or verify `.eslintignore`; if `eslint.config.*` exists, ensure `ignores` covers required generated paths.
- If Prettier config exists, create or verify `.prettierignore`.
- If package publishing is present, create or verify `.npmignore`.
- If Terraform files exist, create or verify `.terraformignore`.
- If Helm charts exist, create or verify `.helmignore`.

Append only missing critical patterns. Do not rewrite existing ignore files wholesale.

Common patterns by stack:

- Node or TypeScript: `node_modules/`, `dist/`, `build/`, `coverage/`, `*.log`, `.env*`.
- Python: `__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `dist/`, `*.egg-info/`, `.env*`.
- Java or Kotlin: `target/`, `.gradle/`, `build/`, `out/`, `*.class`, `*.jar`, `.env*`.
- .NET: `bin/`, `obj/`, `*.user`, `*.suo`, `packages/`.
- Go: `*.exe`, `*.test`, `vendor/`, `*.out`.
- Rust: `target/`, `debug/`, `release/`, `*.rs.bk`, `.env*`.
- C or C++: `build/`, `bin/`, `obj/`, `out/`, `*.o`, `*.so`, `*.a`, `*.exe`, `*.dll`, `*.log`, `.env*`.
- Universal: `.DS_Store`, `Thumbs.db`, `*.tmp`, `*.swp`, `.vscode/`, `.idea/`.

## Task Execution Model

Parse `tasks.md` into:

- Phases: Setup, Foundational, single-story work, Polish, Final Verification.
- Story scope: story goal, independent test criteria, acceptance scenarios, source design coverage, and out-of-scope constraints.
- Test tasks: unit tests for domain behavior and edge cases; integration tests for workflows, acceptance scenarios, persistence, contracts, external interfaces, CLI/API/UI wiring, or system interactions.
- Dependencies: sequential work, parallel `[P]` tasks, file conflicts, and phase gates.
- Final verification: the task that runs `/speckit.verify`, when present.

Execute phase by phase:

1. Complete setup tasks.
2. Complete foundational prerequisites before story work.
3. Write unit tests and run them to confirm they fail for the expected reason.
4. Write integration tests and run them to confirm they fail for the expected reason.
5. Implement only enough production code to satisfy the failing tests and the single story.
6. Run the relevant unit and integration test suites until they pass.
7. Complete polish tasks without changing story scope.
8. Run `/speckit.verify` if it is present as a task; otherwise explicitly recommend it as the final Moon Spec check.

Rules:

- Respect sequential dependencies.
- Parallel `[P]` tasks may run together only when they touch different files and have no unmet dependency.
- Tasks affecting the same file must run sequentially.
- Keep implementation and test evidence mapped to `FR-*`, acceptance scenarios, success criteria, and source design IDs.
- Do not mark a task complete until the work is actually done and any required command has passed or has a documented blocker.
- Mark completed tasks as `[X]` in `tasks.md`.
- Halt on failed non-parallel tasks. For failed parallel tasks, continue successful independent work and report the failures clearly.

## Implementation Rules

- Follow the repository's existing architecture, naming, test style, and tooling.
- Prefer project-local helpers and established abstractions over new patterns.
- Keep the work scoped to the single story.
- Do not weaken requirements, delete source design mappings, or remove the preserved original request.
- Do not add hidden scope beyond the story, source design constraints, or explicit user guidance.
- Write unit tests for domain rules, transformations, validation, edge cases, and failure modes named by the spec.
- Write integration tests for acceptance scenarios, end-to-end workflows, contracts, persistence, external services, startup wiring, or user-facing interfaces when the design implies them.
- Confirm new tests fail before implementing production behavior, then make them pass.
- Refactor only after tests pass.
- Preserve unrelated user changes in the worktree.

## Completion Validation

Before reporting completion:

1. Verify every required task in `tasks.md` is complete.
2. Verify production behavior matches the single story in `spec.md`.
3. Verify the implementation remains aligned with the original feature request or declarative design preserved in `spec.md` `**Input**`.
4. Verify every in-scope `DESIGN-REQ-*` or `DOC-REQ-*` has implementation and test evidence.
5. Run unit tests and integration tests named in `plan.md`, `tasks.md`, `quickstart.md`, or project conventions.
6. Run quickstart validation when present and feasible.
7. Confirm no hidden scope or contradiction was introduced.
8. Run the final `/speckit.verify` task when present.

If a command cannot run because of missing credentials, services, tools, or unsafe side effects, record the exact reason and the smallest next step needed.

## Report

Final response structure:

```markdown
## MoonSpec Implementation

Implemented:
- [task IDs or story slice]: [short summary]

Tests:
- [command]: [PASS/FAIL/NOT RUN]

Verification:
- [speckit.verify result or recommendation]

Changed files:
- [path]

Remaining work:
- [blocking item or "None"]
```

Keep the report concise. Include blockers before summaries when implementation is incomplete.

## Post-Implement Hooks

After completion validation, check `.specify/extensions.yml` for `hooks.after_implement` using the same parsing, filtering, and condition rules as pre-implement hooks. For each executable hook:

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

- Implement exactly one story from the active Moon Spec.
- TDD is the default strategy.
- Unit tests and integration tests are both expected implementation evidence.
- Confirm tests fail for the intended reason before production code.
- Preserve traceability to the original request and source design mappings.
- Mark completed tasks `[X]` only after real completion.
- Run or recommend `/speckit.verify` as the final alignment check.
