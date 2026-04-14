---
description: Extract coverage-checked, independently testable user stories from a technical or declarative design and create one spec per story.
handoffs:
  - label: Plan First Story
    agent: speckit.plan
    prompt: Create an implementation plan for the highest-priority generated spec.
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before design breakdown)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_breakdown` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}

    Wait for the result of the hook command before proceeding to the Outline.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Outline

The text the user typed after `/speckit.breakdown` is the source technical or declarative design. Treat it as the authoritative source for story extraction. If the user provided a path instead of inline text, read that file and use its contents as the source design.

This command turns one broad design into a complete, coverage-checked set of one-story feature specs. Each generated spec MUST follow `templates/spec-template.md` and MUST contain exactly one independently testable user story. Moon Spec uses those specs for test-driven implementation: acceptance scenarios must be concrete enough to derive unit tests and integration tests before production code is written.

1. **Validate input**:
   - If no design text or readable design path is provided: ERROR "No technical design provided"
   - Preserve the original design text verbatim for traceability
   - Do not implement, plan, generate tasks, or create issues

2. **Read and summarize the design**:
   - Summarize the design in a few sentences, focusing on the technical shape, declarative intent, user or operational outcomes, and implementation boundaries
   - Identify explicit non-goals, constraints, migration or rollout notes, security requirements, durability expectations, observability expectations, and external interfaces

3. **Extract coverage points**:
   - Convert the design into normalized major design points with stable IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, and so on
   - For each point, capture:
     - `id`
     - `title`
     - `type` (`requirement`, `constraint`, `integration`, `state-model`, `artifact`, `non-goal`, `security`, `observability`, `migration`, etc.)
     - `source_section` or source heading when available
     - `explanation`
   - Include at least these classes of design points when present:
     - purpose and scope
     - actors, jobs to be done, workflows, and success signals
     - architectural layers and ownership boundaries
     - lifecycle behavior, state transitions, and data model fields
     - protocol, integration, and public contract choices
     - control actions, API surfaces, commands, or UI surfaces
     - reset, migration, rollout, and backwards-compatibility semantics
     - durability, source-of-truth, and persistence rules
     - artifact, observability, logging, and diagnostics expectations
     - security, privacy, policy, and operational constraints
     - explicit exclusions and non-goals

4. **Draft candidate stories**:
   - Create the smallest reasonable set of stories that fully covers the design while preserving clarity and independent validation
   - Split only on independently valuable user or operational outcomes
   - Do not split implementation layers into separate stories unless each layer is independently useful and testable
   - Exclude pure technical chores unless they directly enable a user-visible or operational outcome
   - Explicit non-goals and constraints must still be owned by at least one story, either as acceptance criteria or as a dedicated guardrail/contract story
   - Each story must have one primary concern, one clear delivery surface, and concrete acceptance criteria

5. **Normalize the story list**:
   - Merge duplicates and near-duplicates
   - Keep dependencies explicit: story A depends on story B only when A cannot be independently validated first
   - Assign a concise story title and a 2-4 word short name for each story
   - Rank stories by dependency order, risk, and user value
   - Prefer high-risk contract, state, migration, or integration stories early when they unlock reliable TDD for later stories

6. **Run the coverage gate before writing specs**:
   - Create a coverage matrix from every `DESIGN-REQ-*` point to one or more stories
   - A coverage point passes only when at least one story explicitly owns it in story scope, acceptance criteria, requirements, or source design coverage
   - Implied coverage is not enough
   - A point is weakly owned if a reasonable reader cannot tell which story is responsible for implementing or enforcing it
   - If any coverage point is uncovered, weakly covered, spread so thinly that ownership is unclear, or covered only by future-work language, revise the stories and rerun the gate
   - Do not proceed until the gate result is:
     - `PASS - every major design point is owned by at least one story.`

7. **Create one spec directory per story**:
   - Specs live under `specs/`
   - Use sequential numbering based on existing `specs/` directories unless `.specify/init-options.json` sets `branch_numbering` to `"timestamp"`
   - Directory format: `<prefix>-<short-name>`
   - Copy `templates/spec-template.md` to each directory as `spec.md`
   - Do not create P1/P2/P3 sections inside a spec; create another spec instead

8. **Fill each generated `spec.md`**:
   - Preserve the source design verbatim in the `**Input**` field so `/speckit.verify` can compare the final implementation to the original source
   - Fill exactly one `## User Story - ...` section
   - Include story-specific Summary, Goal, Independent Test, Acceptance Scenarios, Edge Cases, Functional Requirements, Key Entities if relevant, Assumptions, and Success Criteria
   - Add a `### Source Design Coverage` subsection under `## Requirements` listing the `DESIGN-REQ-*` points owned by the spec, with short explanations of how the story covers each point
   - Keep requirements technology-agnostic unless the source design explicitly mandates a technology, protocol, persistence, or deployment constraint
   - Acceptance scenarios must describe behavior that can drive integration tests; functional requirements and edge cases must be specific enough to drive unit tests where applicable
   - Include both unit-testable rules and integration-testable workflows in the story's validation language when the design implies both
   - Record cross-story dependencies under Assumptions or Requirements when they affect validation
   - Mark unclear story-critical choices with `[NEEDS CLARIFICATION: ...]`; do not exceed 3 markers per spec

9. **Create or update the breakdown summary**:
   - Write `specs/breakdown.md` if it does not already exist, otherwise append a new dated section
   - Include:
     - Source design title or path
     - Story extraction date
     - Design summary
     - Coverage points
     - Ordered list of generated specs and their independent test criteria
     - Coverage matrix mapping `DESIGN-REQ-*` points to specs
     - Dependencies between specs
     - Out-of-scope items and rationale
     - Coverage gate result
   - The gate result MUST be exactly:
     - `PASS - every major design point is owned by at least one story.`

10. **Report completion**:
    - List each generated spec path
    - Identify the recommended first spec to run through `/speckit.plan`
    - Note any specs with unresolved `[NEEDS CLARIFICATION]` markers
    - State that each generated spec contains one story only
    - State that TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`
    - State that `/speckit.verify` should be run after implementation to compare the final behavior against the original design preserved in each spec

## Post-Execution Checks

**Check for extension hooks (after design breakdown)**:
Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_breakdown` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Key Rules

- One generated spec equals one user story.
- Preserve the original technical or declarative design verbatim in every generated spec.
- Prefer vertical user or operational outcomes over technical-layer slices.
- Extract stable `DESIGN-REQ-*` coverage points before drafting specs.
- Do not write specs until the coverage gate passes.
- Every major design point, constraint, and non-goal must be explicitly owned by at least one spec.
- Acceptance scenarios must support downstream integration tests; functional requirements and edge cases must support downstream unit tests.
- Do not generate tasks, implementation plans, code, or issues from this command.
- Final implementation alignment is checked later with `/speckit.verify`.
