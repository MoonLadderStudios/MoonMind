# Feature Specification: Merge Gate

**Feature Branch**: `179-merge-automation`
**Created**: 2026-04-16
**Status**: Draft
**Input**: MM-341: Merge Gate

## Original Jira Preset Brief

````text
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

Implement merge-automation automation by adding a dedicated Temporal workflow boundary that waits for external PR readiness signals after a normal implementation task publishes a pull request, then creates a separate pr-resolver follow-up MoonMind execution when the gate opens.

The intended workflow split is:

- Parent task workflow: `MoonMind.Run` for the original user task that performs coding work and publishes the pull request.
- Merge gate workflow: a new workflow such as `MoonMind.MergeAutomation` that waits on GitHub and optional Jira state, records blockers, and decides when a follow-up should start.
- Resolver task workflow: a second `MoonMind.Run` for the pr-resolver follow-up task, because skill invocation is normally executed as part of a MoonMind.Run execution.

The flow must not become one giant workflow. It should operate as:

```text
MoonMind.Run (parent task)
  -> starts MoonMind.MergeAutomation
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

- Invoke the pr-resolver skill with repository, PR, merge method or policy, and the relevant merge-automation context.
- Keep publish mode `none` so the resolver owns remediation and merge behavior without creating another publish step.
- Allow pr-resolver to classify the PR state, merge immediately, remediate comments or CI failures, stop blocked for manual review, exhaust attempts, or fail with a clear reason.

Do not implement this as one parent workflow that stays running until merge automation finishes. The separate workflow boundaries are required so implementation success is not coupled to review latency, cancellation and retry behavior stays legible, and the UI can distinguish:

- Task run: built and published the pull request.
- Merge gate: waiting for external readiness conditions.
- Resolver run: attempting merge or remediation.

Verification should include workflow-boundary coverage for parent-to-gate startup, merge-automation blocker persistence, gate-open resolver creation, duplicate-launch prevention on retry or replay, and resolver-side reuse of gate evaluation after remediation pushes.
````

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Gate Pull Requests Before Automated Resolution

**Summary**: As a MoonMind operator using automated pull request publishing, I want a visible merge gate to wait for external readiness signals before launching pr-resolver so implementation runs can finish promptly while merge automation proceeds independently.

**Goal**: Operators can distinguish completed implementation work from merge-readiness waiting and automated resolution, while MoonMind reliably starts exactly one resolver follow-up only after configured external conditions are satisfied.

**Independent Test**: Run a task configured to publish a pull request with merge automation enabled, simulate blocked and ready external states, and verify that the implementation run completes after publishing, the merge gate records waiting blockers, and exactly one resolver follow-up starts only when the gate opens.

**Acceptance Scenarios**:

1. **Given** a task is configured to publish a pull request with merge automation enabled, **When** the pull request is created successfully, **Then** MoonMind starts one merge gate linked to that pull request and records the pull request identity, latest revision, optional Jira issue key, and selected automation policy.
2. **Given** the merge gate has started after pull request creation, **When** the original implementation run finalizes, **Then** the implementation run completes without waiting for the merge gate or resolver follow-up to finish.
3. **Given** required checks, automated review signals, or optional Jira status are incomplete, **When** the merge gate evaluates readiness, **Then** it records the active blockers and remains in a waiting state visible to operators.
4. **Given** all configured external readiness signals are complete for the current pull request revision, **When** the merge gate opens, **Then** MoonMind creates exactly one resolver follow-up run for pr-resolver and marks the gate as having launched that follow-up.
5. **Given** the merge gate is retried, replayed, or receives duplicate readiness events, **When** it has already launched a resolver follow-up, **Then** it does not launch another resolver for the same pull request revision.
6. **Given** the pull request closes, the tracked revision changes unexpectedly, or automation is blocked by policy, **When** the merge gate evaluates readiness, **Then** it stops or blocks with an operator-readable reason instead of launching pr-resolver.
7. **Given** pr-resolver pushes a remediation commit, **When** new readiness checks are required, **Then** the resolver waits on the same readiness rules without creating another top-level merge gate.

### Edge Cases

- Merge automation is disabled for a task that still publishes a pull request.
- Pull request creation succeeds but the linked Jira issue key is missing or unavailable.
- A readiness signal arrives more than once or arrives before the merge gate is fully initialized.
- The tracked pull request revision changes while the gate is waiting.
- Required external state cannot be fetched or returns a permission or policy denial.
- The resolver follow-up creation succeeds but acknowledgement is retried after a transient failure.
- pr-resolver creates a remediation commit that causes checks or review signals to restart.

## Assumptions

- Merge automation is opt-in per task or policy and remains disabled unless explicitly selected.
- Existing pull request publication continues to own pull request creation before merge automation starts.
- External readiness can be represented by configured checks, automated review completion, and optional Jira status.
- The resolver follow-up uses the existing pr-resolver capability rather than introducing a separate resolver behavior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow a pull-request-publishing task to request merge automation without preventing normal implementation completion.
- **FR-002**: System MUST create a dedicated merge gate after successful pull request publication when merge automation is enabled.
- **FR-003**: The merge gate MUST track the parent run, pull request identity, current pull request revision, optional Jira issue key, selected automation policy, current blockers, and resolver launch state.
- **FR-004**: The parent implementation run MUST be able to complete successfully after the merge gate is started, without waiting for external readiness or resolver completion.
- **FR-005**: The merge gate MUST evaluate configured external readiness signals for the current pull request revision before launching pr-resolver.
- **FR-006**: The merge gate MUST expose waiting states and blocker reasons that distinguish checks, automated review, Jira status, closed pull requests, revision changes, policy denial, and unavailable external state.
- **FR-007**: The merge gate MUST create a separate resolver follow-up run only after all configured readiness signals are complete for the tracked pull request revision.
- **FR-008**: The merge gate MUST prevent duplicate resolver follow-up runs across retries, replays, duplicate events, and repeated readiness evaluations.
- **FR-009**: Resolver follow-up runs MUST carry enough pull request and policy context for pr-resolver to classify, remediate, merge, or stop blocked without relying on the completed parent implementation run.
- **FR-010**: If pr-resolver changes the pull request revision, the resolver path MUST apply the same readiness rules as a transient wait condition rather than creating another top-level merge gate.
- **FR-011**: Operators MUST be able to see implementation run completion, merge-automation waiting or completion, and resolver follow-up progress as separate lifecycle states.
- **FR-012**: System MUST fail or block with an operator-readable reason instead of launching pr-resolver when the pull request is closed, the tracked revision is stale, readiness cannot be confirmed, or automation is denied by policy.

### Key Entities

- **Merge Automation Request**: The task-level intent to run merge automation after pull request publication, including the selected readiness policy.
- **Merge Gate**: A long-lived gate for one published pull request revision that records readiness state, blockers, policy, and resolver launch status.
- **Pull Request Readiness State**: The current external evidence for one pull request revision, including checks, automated review signals, optional Jira status, and terminal blockers.
- **Resolver Follow-up Run**: The separate MoonMind run that invokes pr-resolver after the gate opens.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation, 100% of merge-automation-enabled pull request publications start exactly one merge gate and let the parent implementation run complete independently.
- **SC-002**: In validation, 100% of blocked readiness cases record at least one operator-visible blocker and do not launch pr-resolver.
- **SC-003**: In validation, 100% of ready pull request revisions create exactly one resolver follow-up run, including retry and duplicate-event scenarios.
- **SC-004**: In validation, 100% of stale, closed, policy-denied, or unavailable external-state scenarios stop or block with an operator-readable reason.
- **SC-005**: In validation, resolver remediation commits reuse the same readiness rules without creating an additional top-level merge gate.
