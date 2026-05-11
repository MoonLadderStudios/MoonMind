# Spec 338 — Codex Empty-Turn Retry and Skill No-Op Contract

## Problem

A managed Codex `send_turn` activity can complete with no assistant output
because the underlying API call failed silently (rate limit, transient network,
upstream 5xx, auth expiry). PR #2063 reclassified this case from `failed` to
`completed`, which caused workflows that produced zero useful work to be
recorded as successful runs. Concretely, the `batch-pr-resolver` execution
`mm:aa308c82-2123-4e0f-b469-bc45dcc9df3a` (2026-05-11) was marked completed
even though the agent never ran the resolver script and no child PR-resolver
tasks were queued.

The current runtime cannot distinguish three states:

1. **Success** — the agent produced assistant output describing the work done.
2. **No-op** — the agent intentionally did nothing because the requested work
   was already satisfied (no eligible inputs, idempotent action already
   complete).
3. **Failure** — the agent did not respond, an API call failed, or the run
   produced no evidence of work.

Today all three collapse to “turn completed with no assistant text.” The
runtime treats them all as success (post-#2063) or all as failure (pre-#2063).
Neither is correct.

## Goals

- Stop classifying silent / empty Codex turns as success by default.
- Retry transient send_turn failures with backoff at the Temporal activity
  boundary, so rate-limit and other transient errors recover automatically.
- Provide a positive, opt-in contract by which a skill can declare its run was
  a deliberate no-op so the runtime can record it as such (distinct from
  success and from failure).
- Align with Constitution Principle IX (failure classification distinguishes
  transient from permanent), Principle X (every run ends with a structured
  outcome of success / no-op / failed), and Principle XIII (delete the
  superseded assistantTextMissing plumbing).

## Non-Goals

- Capturing the precise underlying Codex API error code. The Codex CLI/app
  server does not surface response codes to the runtime layer; classification
  is by *outcome* (assistant text present, skill no-op signal present, or
  neither), not by HTTP status.
- Designing a generic skill outcome reporting framework. This spec defines the
  minimum no-op signal; richer per-skill schemas (e.g., pr-resolver’s
  `result.json`) remain skill-specific.
- Changing semantics for managed runtimes other than Codex. The runtime
  classification change is scoped to `CodexManagedSessionRuntime`.

## Requirements

### R1 — Empty assistant output classifies as failure by default

When a Codex `send_turn` (or session-status refresh of a previously-running
turn) reaches a terminal state with empty assistant text **and** no
skill-emitted no-op signal, the runtime MUST return turn status `failed` with
a structured reason describing why classification fell through.

### R2 — Skill no-op contract

A skill MAY declare its run was a deliberate no-op by writing a structured
outcome file to the agent run’s artifact spool directory. The runtime MUST
read this file at turn classification time and, when assistant text is empty
but the file declares no-op, MUST return turn status `completed` with a
disposition of `no_op` and the reason carried through from the file.

Schema (JSON, single object):

- `schema_version` (integer, required, current value `1`)
- `status` (string, required, one of `"success"`, `"no_op"`, `"failed"`)
- `reason` (string, optional, free-form short identifier such as
  `"no_open_prs_matched"`)
- `evidence` (object, optional, skill-specific structured payload)

Path: `<artifact_spool>/skill_outcome.json` (single canonical file per run).
Absence of the file is treated as “no declaration” — the runtime applies its
default classification (R1).

### R3 — Transient send_turn failures retry with backoff

The `agent_runtime.send_turn` Temporal activity MUST retry on transient
failure conditions with exponential backoff (5 attempts, capped at 10 minutes
between attempts). Permanent failure conditions MUST be marked non-retryable
so the activity fails on the first attempt.

The runtime classifies the failure category when finalizing the turn:

- **Transient** — empty assistant text with no skill no-op signal; turn
  start/complete event sequence missing entirely (no rollout file, no
  task_complete event); upstream timeout reported by the Codex app server.
- **Permanent** — Codex session container died, session locator mismatch,
  workflow-cancelled turn, explicit non-retryable error text from Codex.

The runtime MUST surface this category to the activity layer so Temporal’s
retry policy can distinguish them.

### R4 — Workflow records the no-op outcome distinctly

When the runtime returns turn status `completed` with disposition `no_op`,
the `MoonMind.AgentRun` workflow MUST:

- treat the run as **successful** for control-flow purposes (no failure
  artifacts published, no `CodexSessionRunFailedError` raised),
- record the disposition in the run’s memo / search attributes so it is
  visible in Mission Control and Temporal queries (distinguishable from a
  plain success), and
- carry the no-op reason in the run’s outcome summary.

### R5 — `batch-pr-resolver` declares its no-op cases

The `batch-pr-resolver` skill MUST write `skill_outcome.json` next to its
existing `batch_pr_resolver_result.json` when its run is a deliberate no-op.
Deliberate no-op for `batch-pr-resolver` is:

- `created == 0` **and** `errors == []`. I.e., the resolver successfully
  listed open PRs but none matched the eligibility filters.

When `created == 0` **and** `errors` is non-empty, the skill MUST NOT declare
no-op — the failure path applies.

### R6 — Delete superseded plumbing (Principle XIII)

The `assistantTextMissing` / `lastAssistantTextMissing` fields and metadata
flags added by PR #2063 MUST be removed entirely from runtime state,
runtime-handle metadata, session summaries, and tests. There is no fallback
or alias. Code that interpreted those fields downstream MUST be removed or
updated.

## Acceptance Scenarios

### A1 — The originating bug

A `batch-pr-resolver` run whose Codex send_turn produces empty output and
writes no `skill_outcome.json` MUST result in `send_turn` retrying up to 5
times with backoff, and on exhaustion the agent run MUST end with
`CodexSessionRunFailedError`, the workflow status MUST be `failed`, and the
run memo MUST reflect that.

### A2 — Legitimate no-op

A `batch-pr-resolver` run whose Codex send_turn produces empty output **and**
the skill wrote `skill_outcome.json` with `status="no_op"` MUST result in
turn status `completed`, agent run status `succeeded`, run memo recording
`outcome="no_op"` with reason from the file. No retry.

### A3 — Permanent failure short-circuit

If the Codex session container terminates mid-turn, `send_turn` MUST raise a
non-retryable error and Temporal MUST NOT retry. The activity fails on the
first attempt.

### A4 — Successful turn with assistant text

A turn that completes with assistant text MUST continue to be classified as
`completed` with no disposition flag (`disposition` absent from metadata).
This behavior is unchanged.

### A5 — In-flight workflow compatibility

A `MoonMind.AgentRun` workflow that was running at the moment the new
deployment lands MUST NOT fail to replay due to the runtime change. (The
runtime is activity-side, not workflow code, so already-completed activities
keep their recorded results. Activities scheduled after deploy use the new
behavior.)

## Out-of-Scope Follow-Ups

- Other skills under `.agents/skills/*/bin/` are not required to opt in to
  `skill_outcome.json` in this story. They behave correctly without it,
  because they always produce assistant text. Each skill can opt in when its
  team decides which runs are deliberate no-ops.
- A unified skill outcome registry / Mission Control dashboard for no-op vs
  success vs failed runs is left as a downstream story (Principle X long-term
  goal).
