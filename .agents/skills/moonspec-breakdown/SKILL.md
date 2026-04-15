---
name: moonspec-breakdown
description: Extract coverage-checked, independently testable Moon Spec user stories from a technical or declarative design and write a breakdown handoff under docs/tmp. Use when the user asks to run or reproduce `/speckit.breakdown`, split a broad design into one-story candidates, preserve source coverage, or build a coverage matrix before `/speckit.specify`.
---

# MoonSpec Breakdown

Use this skill to perform the Moon Spec breakdown workflow.

## When To Use

Use this skill when the user wants to turn a broad technical or declarative design into multiple independently testable story candidates.

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
- Preserve the original design text verbatim in the breakdown handoff so later `/speckit.specify` output can keep it in `spec.md` `**Input**`.
- Preserve the source document reference path whenever the source design came from a file. Use the repo-relative path when possible; otherwise use the absolute path provided by the user. This reference is required downstream so every Jira story can point back to the original declarative document.
- Do not implement, plan, generate tasks, create Jira issues, create `spec.md`, or create directories under `specs/`.

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
- Source document reference: the original declarative document path plus the relevant source section or heading when available.
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

## Write Breakdown Output

After the coverage gate passes, write story candidates under `docs/tmp/story-breakdowns/`.

Use the explicit `storyBreakdownPath` and `storyBreakdownMarkdownPath` values from the prompt when present. If they are not present, create a timestamped folder under `docs/tmp/story-breakdowns/<short-name>-<YYYYMMDD-HHMMSS>/` and write:

- `stories.json`: machine-readable handoff for Jira issue creation or later specify.
- `stories.md`: human-readable summary.

Never name any breakdown output `spec.md`. Never write to `specs/` during breakdown.

The JSON file must be an object with:

- `source`: object containing `title`, `path`, `referencePath`, and the original design text. For file-backed designs, `path` and `referencePath` must both contain the original design document path. For pasted designs without a file path, set them to `null` and use a clear title such as `inline user request`.
- `extractedAt`: ISO-8601 timestamp.
- `coverageGate`: exactly `PASS - every major design point is owned by at least one story.`
- `stories`: ordered list of story objects.
- `coverageMatrix`: mapping from `DESIGN-REQ-*` points to story IDs.

Each story object must include:

- `id`: stable story ID, such as `STORY-001`.
- `summary`: concise title suitable for a Jira issue summary.
- `description`: user-story or task narrative.
- `sourceReference`: object containing `path`, `title`, `sections`, and `coverageIds`. For file-backed designs, `path` must be the same original design document path from `source.referencePath`; do not omit it from any story.
- `independentTest`: how this story can be validated independently.
- `acceptanceCriteria`: concrete acceptance criteria.
- `requirements`: functional requirements owned by the story.
- `sourceDesignCoverage`: `DESIGN-REQ-*` points with short ownership explanations.
- `dependencies`: story IDs this story truly depends on.
- `assumptions`: only when assumptions are used.
- `needsClarification`: story-critical unresolved choices, max 3 per story.

The markdown file must include the same substance for human review:

- Source design title or path.
- Original source document reference path for the breakdown and for each story.
- Story extraction date.
- Design summary.
- Coverage points.
- Ordered list of story candidates and their independent test criteria.
- Coverage matrix mapping `DESIGN-REQ-*` points to stories.
- Dependencies between stories.
- Out-of-scope items and rationale.
- Coverage gate result.

The gate result must be exactly:

```text
PASS - every major design point is owned by at least one story.
```

## Report

Report completion with:

- The JSON and markdown breakdown paths.
- The recommended first story to run through `/speckit.specify`.
- Any stories with unresolved `[NEEDS CLARIFICATION]` markers.
- Confirmation that no `spec.md` files or `specs/` directories were created.
- Confirmation that TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Confirmation that `/speckit.verify` should be run after implementation to compare final behavior against the original design preserved through specify.

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

- One breakdown story candidate equals one future `spec.md`.
- Preserve the original technical or declarative design verbatim in the breakdown handoff for later specify.
- Every story candidate must carry a `sourceReference.path` back to the original declarative document when the source came from a file, and the story handoff paragraph must mention that path.
- Prefer vertical user or operational outcomes over technical-layer slices.
- Extract stable `DESIGN-REQ-*` coverage points before drafting story candidates.
- Do not write specs in this skill.
- Every major design point, constraint, and non-goal must be explicitly owned by at least one story candidate.
- Acceptance scenarios must support downstream integration tests; functional requirements and edge cases must support downstream unit tests.
- Do not generate tasks, implementation plans, code, or issues from this skill.
- Final implementation alignment is checked later with `/speckit.verify`.
