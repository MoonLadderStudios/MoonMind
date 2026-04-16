# MM-341 MoonSpec Orchestration Input

## Source

- Jira issue: MM-341
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Merge Gate
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

MM-341: Merge Gate

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-341 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Implement merge-gate automation by adding a dedicated Temporal workflow boundary that waits for external PR readiness signals after a normal implementation task publishes a pull request, then creates a separate pr-resolver follow-up MoonMind execution when the gate opens.

The intended workflow split is:

- Parent task workflow: `MoonMind.Run` for the original user task that performs coding work and publishes the pull request.
- Merge gate workflow: a new workflow such as `MoonMind.MergeGate` that waits on GitHub and optional Jira state, records blockers, and decides when a follow-up should start.
- Resolver task workflow: a second `MoonMind.Run` for the pr-resolver follow-up task, because skill invocation is normally executed as part of a MoonMind.Run execution.

The flow must not become one giant workflow. It should operate as:

```text
MoonMind.Run (parent task)
  -> starts MoonMind.MergeGate
       -> later creates MoonMind.Run (pr-resolver follow-up)
```

## Supplemental Acceptance Criteria

- Given a user submits a coding task with Publish = PR and Merge automation = enabled, when the parent `MoonMind.Run` successfully creates the pull request, then it starts exactly one merge gate workflow with the repository, pull request number, pull request URL, head SHA, merge automation policy, and optional linked Jira issue key.
- Given the parent task has opened the pull request and started the merge gate, when parent finalization completes, then the original task can complete successfully without remaining running for long-lived merge automation waiting.
- Given the merge gate is waiting, when required checks, automated review providers, or optional Jira status are incomplete, then the gate records current blockers and waits by webhook or signal when available, with polling as a fallback.
- Given the merge gate evaluates external state, when the pull request is closed, the head SHA changes unexpectedly, required state is unavailable, or policy denies automation, then it fails or blocks deterministically with an operator-readable reason instead of launching pr-resolver.
- Given all configured external review signals have completed for the latest head SHA, when the gate opens, then it creates a separate resolver follow-up execution using `MoonMind.Run` with the pr-resolver skill and publish mode `none`.
- Given the merge gate creates the resolver follow-up execution, when creation succeeds, then the gate records that the resolver has already been launched and completes without launching duplicate follow-ups on retry.
- Given pr-resolver pushes a remediation commit, when new CI or automated review is triggered, then resolver orchestration reuses the same gate evaluation logic as a transient wait reason instead of starting another top-level merge gate workflow.
- Given merge automation state is shown in Mission Control, when an operator reviews the work, then the task run, merge gate, and resolver follow-up are separately visible and distinguish implementation completion from merge automation progress.

## Implementation Notes

Preserve the existing parent `MoonMind.Run` lifecycle:

1. API creates the execution record and starts `MoonMind.Run`.
2. `MoonMind.Run` initializes, plans, executes implementation steps, and publishes the pull request.
3. After successful PR creation, `MoonMind.Run` starts the merge gate once with deterministic idempotency.
4. `MoonMind.Run` finalizes and completes without waiting for merge automation to finish.

Add the merge gate workflow as a separate long-lived Temporal workflow:

- Store parent run ID, repository, PR number, PR URL, current head SHA, optional Jira issue key, merge automation policy, current blockers, and resolver launch state.
- Evaluate GitHub and Jira state through activities rather than workflow code.
- Wake from webhook or signal events when available, and use fallback polling otherwise.
- Expose clear gate states such as waiting for checks, waiting for automated review, waiting for Jira, blocked, resolver launched, and completed.
- Ensure retries and replays cannot create duplicate resolver executions.

Create the resolver follow-up as a separate `MoonMind.Run`:

- Invoke the pr-resolver skill with repository, PR, merge method or policy, and the relevant merge-gate context.
- Keep publish mode `none` so the resolver owns remediation and merge behavior without creating another publish step.
- Allow pr-resolver to classify the PR state, merge immediately, remediate comments or CI failures, stop blocked for manual review, exhaust attempts, or fail with a clear reason.

Do not implement this as one parent workflow that stays running until merge automation finishes. The separate workflow boundaries are required so implementation success is not coupled to review latency, cancellation and retry behavior stays legible, and the UI can distinguish:

- Task run: built and published the pull request.
- Merge gate: waiting for external readiness conditions.
- Resolver run: attempting merge or remediation.

Verification should include workflow-boundary coverage for parent-to-gate startup, merge-gate blocker persistence, gate-open resolver creation, duplicate-launch prevention on retry or replay, and resolver-side reuse of gate evaluation after remediation pushes.
