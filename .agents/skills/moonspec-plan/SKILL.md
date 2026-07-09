---
name: moonspec-plan
description: Generate a MoonSpec implementation plan and design artifacts from a single-story spec. Use when the user asks to run or reproduce `/moonspec.plan`, create or update `plan.md`, produce `research.md`, `data-model.md`, `contracts/`, or `quickstart.md`, evaluate repo principles, define separate unit and integration test strategies, and perform repo-aware gap analysis before `/moonspec.tasks`.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Plan

Use this skill after a feature spec exists and the user wants implementation planning artifacts for exactly one independently testable story.

## When To Use

Use this skill when the user wants to:

- Run or reproduce `/moonspec.plan`.
- Create or update a feature `plan.md`.
- Generate `research.md`, `data-model.md`, `contracts/`, or `quickstart.md`.
- Resolve technical unknowns before task generation.
- Define unit and integration testing strategy for a single-story MoonSpec.
- Determine, from the current repo, whether each in-scope requirement needs code changes, tests only, or no new work.

Do not use this skill to create a spec from a request. Use `moonspec-specify` or `moonspec-breakdown` first.

## Inputs

- Treat the user's text as optional planning guidance.
- Work from the active feature directory resolved by the setup script.
- The feature spec must contain exactly one independently testable user story.
- If the spec is missing, contains multiple user stories, or has unresolved story-critical clarifications after research, stop with an error.
- Use absolute paths in reports.
- Treat `spec.md` as the desired behavior contract. Do not remove a requirement from the story because similar behavior already exists in the codebase.
- `plan.md` and its design artifacts are imperative, temporary execution artifacts (see `docs/Workflows/MoonSpecDocumentModel.md`). Never copy their content into canonical `docs/` files.

## Pre-Plan Hooks

Before setup, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_plan`.
2. If the YAML cannot be parsed or is invalid, skip hook checking silently.
3. Ignore hooks where `enabled` is explicitly `false`; hooks without `enabled` are enabled.
4. Do not evaluate non-empty `condition` expressions. Treat hooks with no condition, null condition, or empty condition as executable. Skip hooks with non-empty conditions.
5. For each executable hook:
   - Optional hook (`optional: true`): print

```markdown
## Extension Hooks

**Optional Pre-Hook**: {extension}
Command: `/{command}`
Description: {description}

Prompt: {prompt}
To execute: `/{command}`
```

   - Mandatory hook (`optional: false`): print

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
.specify/scripts/bash/setup-plan.sh --json
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
- `artifacts/moonspec/source-acceptance.json` and `artifacts/moonspec/acceptance-assessment.json` when present. Treat `VERIFIED` source acceptance rows as regression constraints and use the assessment backlog for planned work.
- `AGENTS.md` when present, especially project principles, testing discipline, and repo constraints
- `IMPL_PLAN`
- Relevant implementation files, tests, contracts, fixtures, migrations, and public interfaces for the story

Validate before planning:

- `FEATURE_SPEC` has exactly one `## User Story - ...` section.
- The story has a Summary, Goal, Independent Test, Acceptance Scenarios, Requirements, and Success Criteria.
- Source design mappings such as `DESIGN-REQ-*` are preserved when present.
- Source packet provenance is preserved when present: source document path, document class, viewpoint, owning surface, stable claim IDs, temporary-adapter role, and issue traceability such as `MM-933` / `MM-927`.
- Relevant `AGENTS.md` principles, testing discipline, and `MUST` constraints are reflected in the plan when present.
- In-scope requirement IDs and scenario IDs can be identified for planning and traceability.

## Fill plan.md

Follow `templates/plan-template.md` structure and preserve its headings.

Fill:

- `Branch`, `Date`, and `Spec`
- `Input` as the single-story spec path
- `Summary`: primary requirement, technical approach, test strategy, and repo gap-analysis outcome
- `Technical Context`:
  - Language/version
  - Primary dependencies
  - Storage
  - Unit testing tool
  - Integration testing tool
  - Target platform
  - Project type
  - Performance goals
  - Constraints
  - Scale/scope
- `Principles Check`: derive relevant checks from `AGENTS.md` project principles, testing discipline, and repo constraints when present
- `Project Structure`: replace placeholder trees with the actual repository layout for the feature
- `Complexity Tracking`: fill only when principle conflicts or unjustified complexity must be justified

Add a `## Requirement Status` section after `## Summary` with one row per in-scope item that materially affects delivery:

- stable source claim IDs from `## Source Packet`, such as `CLAIM-*`, `DESIGN-REQ-*`, or `DOC-REQ-*`
- `FR-*`
- acceptance scenarios or `SCN-*`
- measurable success criteria or `SC-*` when they drive implementation or testing
- in-scope `DESIGN-REQ-*` or `DOC-REQ-*`

Use this table shape:

| ID             | Status                 | Evidence                    | Planned Work                 | Required Tests           |
| -------------- | ---------------------- | --------------------------- | ---------------------------- | ------------------------ |
| FR-001         | missing                | none found                  | add code and tests           | unit + integration       |
| SCN-001        | implemented_unverified | `src/...`, no scenario test | add verification tests first, with implementation contingency if they fail | integration |
| DESIGN-REQ-004 | implemented_verified   | `src/...`, `tests/...`      | no new implementation        | none beyond final verify |

When a row originates from a stable source claim, include the claim ID in the `ID` cell or in the evidence text so `/moonspec.tasks` can map every implementation task back to stable claim provenance. If no stable source claim applies, write `No stable source claim applies: <reason>` in the evidence text.

Allowed `Status` values:

- `missing`: no adequate implementation evidence found
- `partial`: some implementation exists, but the requirement is not fully satisfied
- `implemented_unverified`: behavior appears present, but required proof is missing or insufficient
- `implemented_verified`: behavior is already present and sufficiently covered by existing tests or other evidence

Rules:

- Existing code changes planned work, not story scope.
- Prefer `implemented_unverified` over `implemented_verified` unless current evidence is strong.
- Do not mark `implemented_verified` unless the current code and current tests together satisfy the requirement and its acceptance evidence.
- When uncertain, do not use `implemented_verified`.

Mark technical unknowns as `NEEDS CLARIFICATION` only when they block defensible planning and cannot be resolved from the repo, spec, or conventional project patterns.

## Principles Check

Evaluate relevant `AGENTS.md` principles, testing discipline, and repo constraints before Phase 0 research.

Rules:

- Error on unjustified principle conflicts.
- Record justified conflicts in `Complexity Tracking` with the reason and mitigation.
- Do not continue with unresolved hard gate failures.
- Re-check relevant principles after Phase 1 design artifacts are generated.

## Phase 0: Research And Repo Gap Analysis

Generate `research.md` in `SPECS_DIR`.

Research and resolve:

- Every `NEEDS CLARIFICATION` from `Technical Context`
- Dependency choices and best practices
- Integration patterns
- Test tooling choices, with unit and integration test tools identified separately
- Any source design or repo-principle constraint that affects architecture
- Current repo coverage for each in-scope requirement or scenario

For each in-scope item, inspect relevant code and tests, then classify it:

- `missing` -> requires tests and implementation
- `partial` -> requires tests and implementation changes
- `implemented_unverified` -> requires tests or verification first, plus an explicit implementation contingency to execute if verification exposes a gap
- `implemented_verified` -> requires no new implementation work, but must remain traceable in the plan and final verification

Use this format for each topic in `research.md`:

```markdown
## [Topic or Requirement ID]

Decision: [choice, status, and/or planned work]
Evidence: [relevant file paths, tests, or contracts]
Rationale: [why this choice or status was chosen]
Alternatives considered: [what else was evaluated]
Test implications: [unit, integration, both, or none beyond final verify]
```

After `research.md` is complete:

- Update `plan.md` so no unresolved planning `NEEDS CLARIFICATION` remains.
- Update `## Requirement Status` to match the research.
- Keep all in-scope requirements visible even when already implemented.

## Phase 1: Design And Contracts

Generate design artifacts in `SPECS_DIR`:

- `data-model.md`: entities, fields, relationships, validation rules, and state transitions when the story involves data
- `contracts/`: public interfaces the project exposes to users or other systems
- `quickstart.md`: test-first validation scenarios and commands for the planned implementation

Contract guidance:

- Use the contract format appropriate for the project type.
- Examples include API contracts, CLI command schemas, library public API specs, parser grammars, UI interaction contracts, or event/message contracts.
- Skip `contracts/` only when the feature is purely internal and has no meaningful public or integration surface.

Quickstart guidance:

- Include the intended unit test command.
- Include the intended integration test command.
- Include any service, fixture, environment, or migration setup needed for integration tests.
- Describe how to verify the single story end to end.
- Reflect requirement status decisions:
  - `missing` and `partial`: tests first, then implementation
  - `implemented_unverified`: verification tests first, then a planned implementation contingency if those tests fail
  - `implemented_verified`: final verification only unless the user asks for stronger coverage

## Agent Context

After Phase 1 artifacts are generated and `plan.md` is updated, run the agent context update script:

```bash
.specify/scripts/bash/update-agent-context.sh __AGENT__
```

Replace `__AGENT__` with the active integration key when known. If unknown, run the script without an agent argument so it updates existing agent files.

The script should:

- Parse the current `plan.md`
- Add only new technology from the current plan
- Preserve manual additions between managed markers

## Stop Point

This skill stops after planning and design artifacts. Do not generate `tasks.md` or implementation code.

Report:

- Branch
- `IMPL_PLAN` path
- Generated artifacts
- Unit test strategy
- Integration test strategy
- Principles check result
- Requirement status summary by ID and count
- Which items need code changes
- Which items need tests only
- Which items are already verified
- Readiness for `/moonspec.tasks`

## Post-Plan Hooks

After reporting, check `.specify/extensions.yml` for `hooks.after_plan` using the same parsing, filtering, and condition rules as pre-plan hooks. For each executable hook:

- Optional hook (`optional: true`): print

```markdown
## Extension Hooks

**Optional Hook**: {extension}
Command: `/{command}`
Description: {description}

Prompt: {prompt}
To execute: `/{command}`
```

- Mandatory hook (`optional: false`): print

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
- Use `plan` to decide whether work is code + tests, tests only, or no new implementation.
- Existing code never removes a requirement from `spec.md`; it only changes the planned work.
- Prefer verification tests before implementation when behavior appears to already exist, but include fallback implementation work for `implemented_unverified` items when verification fails.
- Resolve all planning clarifications through `research.md`.
- Error on unjustified principle conflicts, multiple user stories, or unresolved clarifications.
- Generate design artifacts only; leave task generation to `/moonspec.tasks`.
- `tasks.md` should consume `## Requirement Status` when deciding whether to emit implementation tasks, verification-only tasks, or no new work for already-verified items.
