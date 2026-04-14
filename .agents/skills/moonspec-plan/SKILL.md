---
name: moonspec-plan
description: Generate a Moon Spec implementation plan and design artifacts from a single-story spec. Use when the user asks to run or reproduce `/speckit.plan`, create or update `plan.md`, produce `research.md`, `data-model.md`, `contracts/`, or `quickstart.md`, validate constitution gates, or define separate unit and integration test strategies before `/speckit.tasks`.
---

# MoonSpec Plan

Use this skill to perform the Moon Spec planning workflow.

## When To Use

Use this skill after a feature spec exists and the user wants implementation planning artifacts.

Good triggers include:

- Run or reproduce `/speckit.plan`.
- Create or update a feature `plan.md`.
- Generate `research.md`, `data-model.md`, `contracts/`, or `quickstart.md`.
- Resolve technical unknowns before task generation.
- Define unit and integration testing strategy for a single-story Moon Spec.

Do not use this skill to create a spec from a request. Use `moonspec-specify` or `moonspec-breakdown` first.

## Inputs

- Treat the user's text as optional planning guidance.
- Work from the active feature directory resolved by the setup script.
- The feature spec must contain exactly one independently testable user story.
- If the spec is missing, contains multiple user stories, or has unresolved story-critical clarifications after research, stop with an error.
- Use absolute paths in reports and when referencing generated artifacts.

## Pre-Plan Hooks

Before setup, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_plan`.
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

Wait for the result of the hook command before proceeding to the Outline.
```

If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Setup

Run the setup script from the repository root and parse its JSON output:

```bash
scripts/bash/setup-plan.sh --json
```

On PowerShell projects, use:

```powershell
scripts/powershell/setup-plan.ps1 -Json
```

Parse:

- `FEATURE_SPEC`
- `IMPL_PLAN`
- `SPECS_DIR`
- `BRANCH`
- `HAS_GIT`

The setup script creates the feature directory when needed and copies `templates/plan-template.md` to `IMPL_PLAN`.

For shell arguments containing single quotes, use shell-safe escaping such as `'I'\''m Groot'`, or double quotes when possible.

## Load Context

Read:

- `FEATURE_SPEC`
- `.specify/memory/constitution.md`
- `IMPL_PLAN`, which should now contain the copied plan template

Validate before planning:

- `FEATURE_SPEC` has exactly one `## User Story - ...` section.
- The story has a Summary, Goal, Independent Test, Acceptance Scenarios, Requirements, and Success Criteria.
- Source design mappings such as `DESIGN-REQ-*` are preserved when present.
- Constitution gates and `MUST` constraints are available for the plan.

## Fill plan.md

Follow `templates/plan-template.md` structure and preserve its headings.

Fill:

- `Branch`, `Date`, and `Spec`.
- `Input` as the single-story spec path.
- `Summary`: primary requirement, technical approach, and test strategy.
- `Technical Context`:
  - Language/version.
  - Primary dependencies.
  - Storage.
  - Unit testing tool.
  - Integration testing tool.
  - Target platform.
  - Project type.
  - Performance goals.
  - Constraints.
  - Scale/scope.
- `Constitution Check`: derive gates from `.specify/memory/constitution.md`.
- `Project Structure`: replace placeholder trees with the actual repository layout for the feature.
- `Complexity Tracking`: fill only when constitution violations are justified.

Mark technical unknowns as `NEEDS CLARIFICATION` only when they block defensible planning and cannot be resolved from the repo, spec, or conventional project patterns.

## Constitution Gate

Evaluate the constitution before Phase 0 research.

Rules:

- Error on unjustified constitution violations.
- Record justified violations in `Complexity Tracking`.
- Do not continue with unresolved hard gate failures.
- Re-check the constitution after Phase 1 design artifacts are generated.

## Phase 0: Research

Generate `research.md` in `SPECS_DIR`.

Research and resolve:

- Every `NEEDS CLARIFICATION` from `Technical Context`.
- Dependency choices and best practices.
- Integration patterns.
- Test tooling choices, with unit and integration test tools identified separately.
- Any source design or constitution constraint that affects architecture.

Use this format for each resolved topic:

```markdown
## [Topic]

Decision: [what was chosen]
Rationale: [why chosen]
Alternatives considered: [what else was evaluated]
```

After `research.md` is complete, update `plan.md` so no unresolved `NEEDS CLARIFICATION` remains.

## Phase 1: Design And Contracts

Generate design artifacts in `SPECS_DIR`:

- `data-model.md`: entities, fields, relationships, validation rules, and state transitions when the story involves data.
- `contracts/`: public interfaces the project exposes to users or other systems.
- `quickstart.md`: test-first validation scenarios and commands for the planned implementation.

Contract guidance:

- Use the contract format appropriate for the project type.
- Examples include API contracts, CLI command schemas, library public API specs, parser grammars, UI interaction contracts, or event/message contracts.
- Skip `contracts/` only when the feature is purely internal and has no meaningful public or integration surface.

Quickstart guidance:

- Include the intended unit test command.
- Include the intended integration test command.
- Include any service, fixture, environment, or migration setup needed for integration tests.
- Describe how to verify the single story end to end before running `/speckit.tasks`.

## Agent Context

After Phase 1 artifacts are generated and `plan.md` is updated, run the agent context update script:

```bash
scripts/bash/update-agent-context.sh __AGENT__
```

On PowerShell projects:

```powershell
scripts/powershell/update-agent-context.ps1 -AgentType __AGENT__
```

Replace `__AGENT__` with the active integration key when known. If unknown, run the script without an agent argument so it updates existing agent files.

The script should:

- Parse the current `plan.md`.
- Add only new technology from the current plan.
- Preserve manual additions between managed markers.

## Stop Point

This skill stops after planning and design artifacts. Do not generate `tasks.md` or implementation code.

Report:

- Branch.
- `IMPL_PLAN` path.
- Generated artifacts.
- Unit test strategy.
- Integration test strategy.
- Constitution gate result.
- Readiness for `/speckit.tasks`.

## Post-Plan Hooks

After reporting, check `.specify/extensions.yml` for `hooks.after_plan` using the same parsing, filtering, and condition rules as pre-plan hooks. For each executable hook:

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

- Use absolute paths.
- Plan exactly one user story.
- Keep TDD as the default strategy.
- Identify unit and integration testing separately.
- Resolve all planning clarifications through `research.md`.
- Error on gate failures, multiple user stories, or unresolved clarifications.
- Generate design artifacts only; leave task generation to `/speckit.tasks`.
