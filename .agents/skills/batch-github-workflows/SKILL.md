---
name: batch-github-workflows
description: Resolve an inclusive GitHub issue-number range and enqueue one curated GitHub child workflow per open issue with inherited runtime and stable evidence.
metadata:
  sideEffect:
    kind: enqueue_children
    owner: agent
    outcomeArtifact: artifacts/batch-workflows-result.json
    terminalContractId: batch_workflows_fanout.v1
    terminalSchemaVersion: moonmind.batch-workflows-result.v1
  required-capabilities:
    - git
    - gh
---

# Batch GitHub Workflows

## Purpose

Resolve open GitHub Issues in an inclusive number range and queue one curated
child workflow per issue. Every child inherits the parent runtime
(`runtimeInheritance="caller"`) and a shared publish policy. The parent records
durable target and queue evidence; each child owns its configured publish
outcome.

This Skill is the GitHub provider entrypoint. It intentionally does not require
Jira. Use `batch-workflows` for a Jira project/status cohort. Both entrypoints
execute the same portable fan-out engine from the resolved active Skill snapshot.

## Inputs

- `issue_range` (string, required): inclusive `START-END` search criteria.
- `run_ref` (string, required): `preset:github-issue-implement` or
  `preset:github-issue-orchestrate`.
- `repository` (string, optional): GitHub `owner/repository`; default to workflow
  repository context.
- `max_workflows` (number, optional): hard cap on queued children; default `25`.
- `constraints` (string, optional): shared child guidance.
- `run_verify` (boolean, optional): verification toggle for child presets;
  default `true`.
- `publish_mode` (string, optional): `none`, `branch`, `pr`, or
  `pr_with_merge_automation`; default `none`.

## Workflow

1. Treat the inclusive number range as search criteria, not a target list.
   GitHub issues and pull requests share numbers and numbers may be absent.

2. Invoke the GitHub entrypoint exactly once:

   ```bash
   python3 "$MOONMIND_ACTIVE_SKILLS_DIR/batch-github-workflows/bin/batch_workflows.py" \
     --github-issue-range <START-END> \
     --github-repository <owner/repository> \
     --run-ref <curated GitHub run ref> \
     --publish-mode <none|branch|pr|pr_with_merge_automation> \
     --constraints-file <optional constraints path> \
     --run-verify | --no-run-verify \
     --max-workflows <cap>
   ```

   The portable engine uses trusted GitHub GraphQL `issue(number:)` lookups and
   writes `artifacts/batch-workflows-targets.json` before queueing. It includes
   only explicit open Issue objects; closed issues, pull requests, absent
   numbers, and ambiguous states are omitted normally. Numeric spans wider than
   1,000 are rejected before querying.

   The engine binds GitHub issue data into the selected child, stamps
   `runtimeInheritance="caller"` plus the parent's effective runtime fallback,
   applies one publish policy, creates a stable per-target idempotency key, and
   submits through `POST /api/executions` at `MOONMIND_URL`.

   When MoonMind supplies `MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE`
   (preferred) or `MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN`, forward that scoped
   bearer on create and describe calls. A declared token file that is missing or
   empty is a hard failure; never fall back to the ambient value.

   If input validation fails before a trustworthy target list can be written,
   invoke the same entrypoint once with `--preflight-error <message>` and
   `--requested-count <count>`. Do not handcraft or reuse a result artifact.

3. Report the helper's `artifacts/batch-workflows-result.json` queued, skipped,
   and error counts honestly and link every queued child workflow.

## Security and execution constraints

- Require `MOONMIND_URL`; the legacy direct-DB queue is unsupported.
- Never re-select provider, model, or effort; children inherit the caller.
- Cap matched targets at `max_workflows` and bound discovery independently to a
  maximum numeric span of 1,000.
- Skip targets whose run capability cannot be auto-bound with an explicit
  `unsupported_target` reason.
