---
name: code-improvement-proposal
description: Generate one high-value code improvement proposal after a successful task run, scoped to the code changed in this run and its immediate surroundings.
---

# Code Improvement Proposal Skill

## Purpose

After a successful run, propose the single highest-value concrete improvement to
the code worked on in this run. This focuses on the quality of the repository's
code itself, not on MoonMind execution quality and not on the next phase of a
plan.

This skill is distinct from its siblings:

- `fix-proposal` targets MoonMind/system execution-quality issues observed in a run.
- `continuation-proposal` targets the next phase or best next step of the work.
- `code-improvement-proposal` (this skill) targets concrete improvements to the
  repository code itself.

## Inputs

- `inputs.jobId`
- `inputs.repository`
- `inputs.runtimeMode`
- `inputs.taskStatus` (expected `completed`)
- `inputs.taskSummary`
- `inputs.taskError`
- `inputs.taskContextPath` (usually `../artifacts/task_context.json`)
- `inputs.artifactsPath` (usually `../artifacts`)
- `inputs.proposalOutputPath` (usually `../artifacts/workflow_proposals.json`)

## Detection Focus

Look at the code that was changed in this run and its immediate surroundings.
Prefer concrete, well-bounded improvements such as:

- Refactors that reduce duplication or complexity
- Dead, unreachable, or unused code that can be removed
- Missing or weak test coverage for the changed behavior
- Readability and naming clarity
- Error handling and input validation gaps
- Type-safety improvements
- Tightly-scoped performance gains backed by evidence

Avoid broad rewrites, speculative redesigns, and work unrelated to the code
touched in this run.

## Workflow

1. Read `task_context.json`, the run summary, and the produced diff/changed files.
2. Identify the single best, well-scoped code improvement with clear impact.
3. If no meaningful, in-scope improvement exists, keep the proposal file unchanged.
4. If one exists, append one proposal object to the JSON array at `proposalOutputPath`.
5. Keep the output valid JSON and avoid duplicate titles.

## Proposal Requirements

Each appended item must include:

- `title`
- `summary`
- `workflowCreateRequest` (valid queue workflow creation envelope)

When the proposal targets the MoonMind repository itself, follow the MoonMind
run-quality contract:

- `category: "run_quality"`
- at least one approved `tag`
- `signal` metadata including at least `severity` (`low|medium|high|critical`)

For improvements targeting other repositories, use `category: "code_improvement"`.

## Constraints

- Do not modify repository source files.
- Do not commit or push.
- Keep proposals concrete, scoped, and implementation-ready.
- Stay within the code touched in this run and its immediate surroundings; do not
  broaden scope.
