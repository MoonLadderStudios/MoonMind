---
name: fix-proposal
description: Generate one high-value follow-up task proposal when MoonMind execution quality issues are detected in a run.
---

# Fix Proposal Skill

## Purpose

Produce a targeted follow-up task proposal for MoonMind/system quality problems observed during a task run.

## Inputs

- `inputs.jobId`
- `inputs.repository`
- `inputs.runtimeMode`
- `inputs.taskStatus` (`succeeded` or `failed`)
- `inputs.taskSummary`
- `inputs.taskError`
- `inputs.taskContextPath` (usually `../artifacts/task_context.json`)
- `inputs.artifactsPath` (usually `../artifacts`)
- `inputs.proposalOutputPath` (usually `../artifacts/task_proposals.json`)

## Detection Focus

Look for partial or complete execution failures and degraded behavior, including:

- Access/auth failures
- Unclear or conflicting instructions
- Retry storms or repeated retries
- Execution loops
- Duplicate/repetitive output
- Missing required references or artifact gaps

## Workflow

1. Read `task_context.json` and available logs under `artifacts/logs/`.
2. Determine whether there is a clear, high-value fix opportunity.
3. If no meaningful issue is found, keep the proposal file unchanged.
4. If an issue is found, append one proposal object to the JSON array at `proposalOutputPath`.
5. Keep the output valid JSON and avoid duplicate titles.

## Proposal Requirements

Each appended item must include:

- `title`
- `summary`
- `taskCreateRequest` (valid queue task creation envelope)

When proposing MoonMind/run-quality improvements, also include:

- `category: "run_quality"`
- `tags`: at least one from:
  - `retry`
  - `duplicate_output`
  - `missing_ref`
  - `conflicting_instructions`
  - `flaky_test`
  - `loop_detected`
  - `artifact_gap`
- `signal`: include at least `severity` (`low|medium|high|critical`) and brief evidence context.

## Constraints

- Do not modify repository source files.
- Do not commit or push.
- Keep proposals concise and actionable.
