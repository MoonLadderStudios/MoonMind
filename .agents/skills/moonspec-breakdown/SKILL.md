---
name: moonspec-breakdown
description: Extract coverage-checked, independently testable Moon Spec user stories from a technical or declarative design and create one spec per story. Use when the user asks to run or reproduce `/speckit.breakdown`, split a broad design into one-story specs, preserve the original design for later verification, or build a coverage matrix from design requirements to generated specs.
---

# MoonSpec Breakdown

Use this skill to perform the Moon Spec breakdown workflow.

## When To Use

Use this skill when the user wants to turn a broad technical or declarative design into multiple Moon Spec feature specs.

Good inputs include:

- A pasted technical design.
- A declarative design document.
- A file path to a design artifact.
- A request to run or reproduce `/speckit.breakdown`.

Do not use this skill for a single natural-language feature request. Use `moonspec-specify` for one clearly scoped story.

## Inputs

- Treat the user's request text as the source design unless it names a readable file path.
- If a file path is provided, resolve it relative to the repo root unless it is absolute, then read it before extracting stories.
- If no design text or readable design path is provided, stop with: `ERROR "No technical design provided"`.
- Preserve the original design text verbatim. Every generated `spec.md` must keep it in the `**Input**` field so `/speckit.verify` can compare final behavior to the source design.
- Do not implement, plan, generate tasks, or create issues.

## Pre-Breakdown Hooks

Before extracting stories, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_breakdown`.
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

## Breakdown Workflow

### 1. Summarize The Design

Summarize the design in a few sentences, focusing on:

- Technical shape and declarative intent.
- User or operational outcomes.
- Implementation boundaries.
- Explicit non-goals and constraints.
- Migration, rollout, security, durability, observability, and external interface expectations.

### 2. Extract Coverage Points

Convert the design into normalized major design points with stable IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, and so on.

For each coverage point, capture:

- `id`
- `title`
- `type`: `requirement`, `constraint`, `integration`, `state-model`, `artifact`, `non-goal`, `security`, `observability`, `migration`, or another precise type.
- `source_section` or source heading when available.
- `explanation`

Include these design point classes when present:

- Purpose and scope.
- Actors, jobs to be done, workflows, and success signals.
- Architectural layers and ownership boundaries.
- Lifecycle behavior, state transitions, and data model fields.
- Protocol, integration, and public contract choices.
- Control actions, API surfaces, commands, or UI surfaces.
- Reset, migration, rollout, and backwards-compatibility semantics.
- Durability, source-of-truth, and persistence rules.
- Artifact, observability, logging, and diagnostics expectations.
- Security, privacy, policy, and operational constraints.
- Explicit exclusions and non-goals.

### 3. Draft Candidate Stories

Create the smallest reasonable set of stories that fully covers the design while preserving clarity and independent validation.

Rules:

- Split only on independently valuable user or operational outcomes.
- Do not split implementation layers into separate stories unless each layer is independently useful and testable.
- Exclude pure technical chores unless they directly enable a user-visible or operational outcome.
- Explicit non-goals and constraints must still be owned by at least one story, either as acceptance criteria or as a dedicated guardrail or contract story.
- Each story must have one primary concern, one clear delivery surface, and concrete acceptance criteria.

For each story, define:

- Title.
- 2-4 word short name for directory naming.
- Why the story exists.
- Scope and out of scope.
- Independent test.
- Acceptance criteria.
- Dependencies.
- Risks or open questions.
- Owned `DESIGN-REQ-*` coverage points.
- A short handoff paragraph suitable for a generated one-story `spec.md`.

### 4. Normalize And Order Stories

- Merge duplicates and near-duplicates.
- Keep dependencies explicit: story A depends on story B only when A cannot be independently validated first.
- Rank stories by dependency order, risk, and user value.
- Prefer high-risk contract, state, migration, or integration stories early when they unlock reliable TDD for later stories.

### 5. Run The Coverage Gate

Create a coverage matrix from every `DESIGN-REQ-*` point to one or more stories.

A coverage point passes only when at least one story explicitly owns it in story scope, acceptance criteria, requirements, or source design coverage. Implied coverage is not enough.

A point is weakly owned if a reasonable reader cannot tell which story is responsible for implementing or enforcing it.

If any coverage point is uncovered, weakly covered, spread so thinly that ownership is unclear, or covered only by future-work language, revise the stories and rerun the gate.

Do not write specs until the gate result is exactly:

```text
PASS - every major design point is owned by at least one story.
```

## Create Specs

Create one spec directory per story after the coverage gate passes.

Rules:

- Specs live under `specs/`.
- Use sequential numbering based on existing `specs/` directories unless `.specify/init-options.json` sets `branch_numbering` to `"timestamp"`.
- Sequential directory prefix is the next available 3-digit number.
- Timestamp directory prefix is `YYYYMMDD-HHMMSS`.
- Directory format is `<prefix>-<short-name>`.
- Copy `templates/spec-template.md` to each directory as `spec.md`.
- Do not create P1/P2/P3 sections inside a spec; create another spec instead.

Fill each generated `spec.md` with exactly one `## User Story - ...` section.

Include:

- Story-specific Summary.
- Goal.
- Independent Test.
- Acceptance Scenarios.
- Edge Cases.
- Functional Requirements.
- Key Entities when relevant.
- Assumptions when assumptions are used.
- Success Criteria.

Under `## Requirements`, add a `### Source Design Coverage` subsection listing the `DESIGN-REQ-*` points owned by the spec, with short explanations of how the story covers each point.

Guidelines:

- Preserve the source design verbatim in the `**Input**` field.
- Keep requirements technology-agnostic unless the source design explicitly mandates a technology, protocol, persistence, or deployment constraint.
- Acceptance scenarios must describe behavior that can drive integration tests.
- Functional requirements and edge cases must be specific enough to drive unit tests where applicable.
- Include both unit-testable rules and integration-testable workflows when the design implies both.
- Record cross-story dependencies under Assumptions or Requirements when they affect validation.
- Mark unclear story-critical choices with `[NEEDS CLARIFICATION: ...]`; do not exceed 3 markers per spec.

## Breakdown Summary

Write `specs/breakdown.md` if it does not already exist. If it exists, append a new dated section.

Include:

- Source design title or path.
- Story extraction date.
- Design summary.
- Coverage points.
- Ordered list of generated specs and their independent test criteria.
- Coverage matrix mapping `DESIGN-REQ-*` points to specs.
- Dependencies between specs.
- Out-of-scope items and rationale.
- Coverage gate result.

The gate result must be exactly:

```text
PASS - every major design point is owned by at least one story.
```

## Report

Report completion with:

- Each generated spec path.
- The recommended first spec to run through `/speckit.plan`.
- Any specs with unresolved `[NEEDS CLARIFICATION]` markers.
- Confirmation that each generated spec contains one story only.
- Confirmation that TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Confirmation that `/speckit.verify` should be run after implementation to compare final behavior against the original design preserved in each spec.

## Post-Breakdown Hooks

After reporting, check `.specify/extensions.yml` for `hooks.after_breakdown` using the same parsing, filtering, and condition rules as pre-breakdown hooks. For each executable hook:

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

- One generated spec equals one user story.
- Preserve the original technical or declarative design verbatim in every generated spec.
- Prefer vertical user or operational outcomes over technical-layer slices.
- Extract stable `DESIGN-REQ-*` coverage points before drafting specs.
- Do not write specs until the coverage gate passes.
- Every major design point, constraint, and non-goal must be explicitly owned by at least one spec.
- Acceptance scenarios must support downstream integration tests; functional requirements and edge cases must support downstream unit tests.
- Do not generate tasks, implementation plans, code, or issues from this skill.
- Final implementation alignment is checked later with `/speckit.verify`.
