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
- `repository_connection_ref` (string, optional): canonical repository connection
  authority; default to workflow context or `MOONMIND_REPOSITORY_CONNECTION_REF`.
- `max_workflows` (number, optional): hard cap on queued children; default `25`.
- `constraints` (string, optional): shared child guidance.
- `run_verify` (boolean, optional): verification toggle for child presets;
  default `true`.
- `publish_mode` (string, optional): `none`, `branch`, `pr`, or
  `pr_with_merge_automation`; default `pr`. Both curated run options
  implement code, so children publish a pull request by default. The batch
  parent itself publishes nothing (`workflowPublish.mode: none`).

## Workflow

1. Treat the inclusive number range as search criteria, not a target list.
   GitHub issues and pull requests share numbers and numbers may be absent.

2. Invoke the GitHub entrypoint exactly once:

   ```bash
   python3 "$MOONMIND_ACTIVE_SKILLS_DIR/batch-github-workflows/bin/batch_workflows.py" \
     --github-issue-range <START-END> \
     --github-repository <owner/repository> \
     --repository-connection-ref <repository connection ref> \
     --run-ref <curated GitHub run ref> \
     --publish-mode <none|branch|pr|pr_with_merge_automation> \
     --constraints-file <optional constraints path> \
     --run-verify | --no-run-verify \
     --max-workflows <cap>
   ```

   The portable engine uses trusted GitHub GraphQL `issue(number:)` lookups and
   resolves the repository's current default branch in the same query before it
   writes `artifacts/batch-workflows-targets.json` and queues children. Every
   child receives the canonical Git repository target, including the caller's
   exact connection authority and resolved branch. The connection is read from
   the explicit argument, parent task context, or
   `MOONMIND_REPOSITORY_CONNECTION_REF`; discovery fails before queueing when it
   is unavailable. A missing, unreadable, changing, or Git-invalid default
   branch also fails discovery before queueing. The engine includes only
   explicit open Issue objects; closed issues, pull requests, absent numbers,
   and ambiguous states are omitted normally. Numeric spans wider than 1,000 are
   rejected before querying.

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

## Multi-repository batch (isolated fan-out)

MoonLadderStudios/MoonMind#1657 selects isolated fan-out as the first
multi-repository increment: one operation applies a bounded task to an
explicit set of repositories, with a separately admitted child workflow and
workspace per repository. The parent publishes nothing and forwards no raw
credential bundle; each child carries only its own explicit `connectionRef`
and is re-admitted through the existing repository and execution contracts.

This path reuses the same portable engine and the normal execution
API/Temporal substrate. It does not mount several writable repositories into
one agent, does not discover every repository a token can see, and does not
promise cross-repository atomicity.

1. Author an explicit bounded target list as a JSON array (default cap 10,
   hard cap 25). Each entry names `repository` (`owner/repo`, no wildcards),
   an explicit `connectionRef` (never an ambient fallback), a safe `branch`,
   and an `operation` (`read`, `branch`, `pr`, `pr_with_merge_automation`).
   Optional keys: `endpoint` (default `https://github.com`; identical names
   on different hosts stay distinct), `revision` (pinned SHA intent),
   `dependsOn` (explicit upstream target refs), and `evidenceKind`
   (`revision` or `artifact`). Raw credential keys are rejected.

2. Preflight first to freeze the immutable target manifest for approval:

   ```bash
   python3 "$MOONMIND_ACTIVE_SKILLS_DIR/batch-github-workflows/bin/batch_workflows.py" \
     --run-ref preset:github-issue-implement \
     --repository-targets-file <targets.json> \
     --preflight-only
   ```

   The engine normalizes duplicates (exact duplicates collapse with
   evidence; same name on different hosts stays distinct; conflicting
   connections for one repository fail closed), writes
   `artifacts/batch-repository-manifest.json` with its `digest`, and
   dispatches nothing. The operator's approval covers that exact digest:
   the task snapshot, publication mode, limits, and target set.

3. Dispatch with the approved digest. Preflight classifies every target
   before launch; any inaccessible target blocks the batch unless the
   operator explicitly passes `--allow-partial` to proceed with the
   accessible subset:

   ```bash
   python3 "$MOONMIND_ACTIVE_SKILLS_DIR/batch-github-workflows/bin/batch_workflows.py" \
     --run-ref preset:github-issue-implement \
     --repository-targets-file <targets.json> \
     --approved-batch-digest <sha256:...> \
     [--allow-partial] [--retry-failed-only] \
     [--upstream-evidence-file <evidence.json>] \
     [--batch-budget-file <budget.json>]
   ```

   A target injected after approval changes the digest, so
   `--approved-batch-digest` mismatches and dispatch refuses. Each target
   gets a stable child identity bound to the manifest digest, its own
   `repositoryTarget` (separate workspace), artifact namespace, and cleanup
   owner. Every admission is verified via `GET /api/executions/{workflowId}`
   before it counts as queued; ambiguous submissions retry under the same
   idempotency key, and a parent restart discovers accepted children from
   the prior `artifacts/batch-repositories-result.json` instead of
   duplicating work. Concurrency is bounded by `maxConcurrency` with a
   bounded capacity wait; exhausted capacity marks remaining targets
   `blocked` truthfully. Cancel owned queued/running children with
   `--cancel-owned` instead of dispatching.

4. Dependent phases use explicit `dependsOn` edges plus verified evidence:
   `--upstream-evidence-file` maps upstream refs to `{verified: true,
   kind: revision|artifact, revision|artifactRef}`. A bare PR number never
   satisfies a merged-code dependency; without verified evidence the
   dependent stays `blocked`.

5. Report `artifacts/batch-repositories-result.json` honestly:
   `queued/running/succeeded/failed/blocked/canceled` plus publication
   results per target. Batch dispatch success is not task completion.
   Retrying selected failed children (`--retry-failed-only`) admits fresh
   children for those targets only and never republishes completed ones.

## Security and execution constraints

- Require `MOONMIND_URL`; the legacy direct-DB queue is unsupported.
- Never re-select provider, model, or effort; children inherit the caller.
- Cap matched targets at `max_workflows` and bound discovery independently to a
  maximum numeric span of 1,000.
- Skip targets whose run capability cannot be auto-bound with an explicit
  `unsupported_target` reason.
