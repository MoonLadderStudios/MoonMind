---
name: moonspec-breakdown
description: Extract coverage-checked, independently testable MoonSpec user stories from a technical or declarative design and write breakdown output under artifacts/story-breakdowns (gitignored). Use when the user asks to run or reproduce `/moonspec.breakdown`, split a broad design into one-story candidates, preserve source coverage, or build a coverage matrix before `/moonspec.specify`.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Breakdown

Use this skill to perform the MoonSpec breakdown workflow.

## When To Use

Use this skill when the user wants to turn a broad technical or declarative design into multiple independently testable story candidates.

Good inputs include:

- A pasted technical design.
- A declarative design document.
- A file path to a design artifact.
- A trusted Jira issue brief or description when no source document path is provided.
- A request to run or reproduce `/moonspec.breakdown`.

Do not use this skill for a single natural-language feature request. Use `moonspec-specify` for one clearly scoped story.

## Inputs

- Select breakdown input content using this preference chain:
  1. Source document path: if an explicit source document path is provided, resolve it relative to the repo root unless it is absolute, then read it before extracting stories.
  2. Jira issue description: if no source document path is provided and trusted Jira issue context is available from MoonMind previous outputs, use `jiraPresetBrief` or the Jira issue description/acceptance criteria from that trusted context.
  3. Workflow instructions: if neither of the above is available, use the workflow instructions or user request text as the source design.
- If no selected input content is available after applying the preference chain, stop with: `ERROR "No technical design provided"`.
- Preserve the original design text verbatim in the breakdown output so later `/moonspec.specify` output can keep it in `spec.md` `**Input**`.
- Preserve the source document reference path whenever the selected source design came from a file. Use the repo-relative path when possible; otherwise use the absolute path provided by the user. If the selected source is trusted Jira context or workflow instructions, set source and story reference paths to `null` and preserve the source title/key instead.
- The canonical source document, not the breakdown output, remains the source of truth for desired state. Breakdown output is a temporary derived view (see `docs/Workflows/MoonSpecDocumentModel.md`).
- Do not implement, plan, generate tasks, create Jira issues, create `spec.md`, or create directories under `specs/`.

## Input Classification

Classify the source design before extracting coverage points, using the document classes in `docs/Workflows/MoonSpecDocumentModel.md`:

- `canonical-declarative`: a readable repo path under `docs/` describing desired state.
- `declarative-text`: pasted declarative design text, trusted Jira issue description, workflow instructions, or a file-backed declarative design outside `docs/`.
- `imperative-input`: a selected source whose primary framing is steps, phases, checkboxes, status, or migration sequencing.

A document is declarative when it describes what the system is or should be; it is imperative when its primary framing is steps, phases, checkboxes, or status. Mixed documents are classified by their primary framing.

Prefer declarative sources when the input preference chain provides one. If no
declarative design is selected, use the imperative input instead of requiring
explicit confirmation. For `imperative-input`, infer the underlying desired
system behavior, constraints, and operator outcomes from the steps. Do not
create one story per checklist item unless that item is independently valuable
and testable as a user or operational outcome. Preserve any story-critical
uncertainty as assumptions or `needsClarification` rather than stopping solely
because the source is imperative.

Record the resulting class as `sourceDocumentClass` in the breakdown output.

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

### 2. Extract Canonical Claims And Coverage Points

For canonical file-backed declarative documents, identify the selected stable
canonical claims before creating run-local coverage points:

- If the source document already contains stable claim identifiers, preserve
  those exact identifiers.
- If the source document does not contain explicit claim identifiers, derive
  stable claim IDs from the repo-relative source path, source heading, and claim
  order within that heading. Use deterministic, path-anchored IDs that remain
  stable across reruns when surrounding unrelated text changes.
- Record each selected claim with `id`, `path`, `sections`, and a concise claim
  summary. The `path` must be the canonical repo-relative document path when
  the source came from a repo file.
- For non-canonical file-backed sources, trusted Jira descriptions, pasted
  workflow instructions, or `imperative-input`, do not fabricate canonical claim
  IDs. Use only run-local coverage IDs and preserve the selected source
  title/key and source path when one exists.

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
- Source document reference: the original source document path plus the relevant source section or heading when available.
- Scope and out of scope.
- Independent test.
- Acceptance criteria.
- Dependencies.
- Risks or open questions.
- Owned `DESIGN-REQ-*` coverage points.

### 4. Normalize And Order Stories

- Merge duplicates and near-duplicates.
- Keep dependencies explicit: story A depends on story B only when A cannot be independently validated first.
- Rank stories by dependency order, risk, and user value.
- Preserve stable story IDs, story order, and dependency IDs in `stories.json` so downstream Jira export can map created Jira issue keys back to ordered stories and optionally create a linear blocker chain.
- Prefer high-risk contract, state, migration, or integration stories early when they unlock reliable TDD for later stories.

### 5. Run The Coverage Gate

Create a coverage matrix from every selected canonical claim ID and every
`DESIGN-REQ-*` point to one or more stories.

A coverage point or canonical claim passes only when at least one story
explicitly owns it in story scope, acceptance criteria, requirements, or source
design coverage. Implied coverage is not enough.

A point is weakly owned if a reasonable reader cannot tell which story is responsible for implementing or enforcing it.

If any coverage point is uncovered, weakly covered, spread so thinly that ownership is unclear, or covered only by future-work language, revise the stories and rerun the gate.

Do not write specs until the gate result is exactly:

```text
PASS - every major design point is owned by at least one story.
```

## Write Breakdown Output

After the coverage gate passes, write story candidates under `artifacts/story-breakdowns/` (repository gitignore; not versioned).

Use the explicit `storyBreakdownPath` and `storyBreakdownMarkdownPath` values from the prompt when present. If they are not present, create a timestamped folder under `artifacts/story-breakdowns/<short-name>-<YYYYMMDD-HHMMSS>/` and write:

- `stories.json`: machine-readable breakdown output for Jira issue creation or later specify.
- `stories.md`: human-readable summary.

Never name any breakdown output `spec.md`. Never write to `specs/` during breakdown.

The JSON file must be an object with:

- `source`: object containing `title`, `path`, `referencePath`, `sourceDocumentClass`, and the original design text. For file-backed designs, `path` and `referencePath` must both contain the original design document path. For trusted Jira issue descriptions or pasted workflow instructions without a file path, set paths to `null` and use a clear title such as the Jira issue key/summary or `inline workflow instructions`. `sourceDocumentClass` must be `canonical-declarative`, `declarative-text`, or `imperative-input` per the Input Classification section.
- `extractedAt`: ISO-8601 timestamp.
- `coverageGate`: exactly `PASS - every major design point is owned by at least one story.`
- `stories`: ordered list of story objects.
- `coverageMatrix`: mapping from every selected canonical claim ID or
  `DESIGN-REQ-*` point to story IDs.

Each story object must include:

- `id`: stable story ID, such as `STORY-001`.
- `summary`: concise title suitable for a Jira issue summary.
- `description`: user-story or task narrative.
- `sourceReference`: object containing `path`, `title`, `sections`, `claimIds`, and `coverageIds`. For canonical file-backed declarative designs, `path` must be the same original design document path from `source.referencePath`, and `claimIds` must contain the selected stable canonical claim IDs owned by the story; do not omit either value from any story. For non-canonical file-backed sources and `imperative-input`, preserve the source path when one exists, set `claimIds` to an empty list, and rely on `coverageIds` for run-local traceability. For trusted Jira issue descriptions or workflow instructions without a file path, set `path` to `null`, set `claimIds` to an empty list, and preserve the selected source title.
- `independentTest`: how this story can be validated independently.
- `acceptanceCriteria`: concrete acceptance criteria.
- `requirements`: functional requirements owned by the story.
- `sourceDesignCoverage`: `DESIGN-REQ-*` points with short ownership explanations.
- `dependencies`: story IDs this story truly depends on.
- `assumptions`: only when assumptions are used.
- `needsClarification`: story-critical unresolved choices, max 3 per story.

The markdown file must include the same substance for human review:

- Source design title or path.
- Original source document reference path for the breakdown and for each story when a file path exists; otherwise show the selected Jira issue key/summary or workflow instruction title.
- Story extraction date.
- Design summary.
- Coverage points.
- Ordered list of story candidates and their independent test criteria.
- Coverage matrix mapping every selected canonical claim ID or `DESIGN-REQ-*` point to stories.
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
- The recommended first story to run through `/moonspec.specify`.
- Any stories with unresolved `[NEEDS CLARIFICATION]` markers.
- Confirmation that no `spec.md` files or `specs/` directories were created.
- Confirmation that TDD remains the default strategy for downstream `/moonspec.plan`, `/moonspec.tasks`, and `/moonspec.implement`.
- Confirmation that `/moonspec.verify` should be run after implementation to compare final behavior against the original design preserved through specify.

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
- The canonical source document remains the source of truth for desired state; breakdown output is a temporary derived view and is never cited as authority over its source.
- Classify the input per `docs/Workflows/MoonSpecDocumentModel.md`; prefer declarative sources, but use `imperative-input` when no declarative design is selected instead of requiring explicit confirmation.
- Preserve the original technical or declarative design verbatim in the breakdown output for later specify.
- Every story candidate from a canonical declarative file must carry a
  `sourceReference.path` and non-empty `sourceReference.claimIds` back to stable
  canonical claims in the original declarative document. Non-canonical
  file-backed and imperative inputs preserve the selected source path when one
  exists and use run-local `coverageIds` with empty `claimIds`. Source text
  without a file path must carry the selected source title/key with `path: null`
  and empty `claimIds`.
- Prefer vertical user or operational outcomes over technical-layer slices.
- Extract stable canonical claim IDs for canonical file-backed documents and run-local
  `DESIGN-REQ-*` coverage points before drafting story candidates.
- Do not write specs in this skill.
- Every major design point, constraint, and non-goal must be explicitly owned by at least one story candidate.
- Acceptance scenarios must support downstream integration tests; functional requirements and edge cases must support downstream unit tests.
- Do not generate tasks, implementation plans, code, or issues from this skill.
- Final implementation alignment is checked later with `/moonspec.verify`.
