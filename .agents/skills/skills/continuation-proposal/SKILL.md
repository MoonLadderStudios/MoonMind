---
name: continuation-proposal
description: Generate one best-next-step proposal after a successful task run, either the next phase in a series or a high-value improvement.
---

# Continuation Proposal Skill

## Purpose

After a successful run, propose the highest-value continuation task.

## Inputs

- `inputs.jobId`
- `inputs.repository`
- `inputs.runtimeMode`
- `inputs.taskStatus` (expected `succeeded`)
- `inputs.taskSummary`
- `inputs.taskError`
- `inputs.taskContextPath` (usually `../artifacts/task_context.json`)
- `inputs.artifactsPath` (usually `../artifacts`)
- `inputs.proposalOutputPath` (usually `../artifacts/task_proposals.json`)

## Decision Rule

1. Determine whether the completed work is part of a multi-step or phased series.
2. If it is part of a series, propose the next concrete task/phase.
3. If it is not, propose one meaningful follow-up:
   - refactor
   - performance improvement
   - feature extension

## Workflow

1. Read `task_context.json`, execution logs, and task summary.
2. Identify the single best continuation with clear impact.
3. Append one proposal object to `proposalOutputPath`.
4. Keep the output as a valid JSON array and avoid duplicate titles.

## Proposal Requirements

Each appended item must include:

- `title`
- `summary`
- `taskCreateRequest` (valid queue task creation envelope)

For MoonMind/run-quality continuations, include:

- `category: "run_quality"`
- approved `tags` and `signal` metadata when applicable.

## Constraints

- Do not modify repository source files.
- Do not commit or push.
- Keep proposals concrete, scoped, and implementation-ready.
