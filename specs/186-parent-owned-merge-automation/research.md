# Research: Parent-Owned Merge Automation

## Input Classification

Decision: Treat the MM-350 Jira preset brief as a single-story runtime feature request.
Rationale: The brief contains one user story, one independent test, one acceptance-criteria set, and explicit runtime source requirements from `docs/Tasks/PrMergeAutomation.md`.
Alternatives considered: Treating the source document as a broad technical design was rejected because the Jira brief already selects one independently testable story. Reusing `specs/179-merge-gate` was rejected because that feature implements MM-341's detached merge-gate semantics rather than MM-350's parent-owned awaiting semantics.

## Parent Ownership Boundary

Decision: Implement merge automation as child workflow work owned and awaited by the original `MoonMind.Run`.
Rationale: MM-350's value is that downstream tasks can depend on the original workflow identity and receive a completion signal only after publish plus merge automation finish.
Alternatives considered: Starting a detached `MoonMind.MergeGate` and letting the parent complete was rejected because it satisfies MM-341 but violates MM-350 dependency semantics. Creating a fixed-delay follow-up task was rejected because it does not provide state-based readiness or parent-owned completion.

## Workflow Payload Shape

Decision: Persist a compact publish context before child start and pass compact request/result models across the parent-child boundary.
Rationale: Temporal workflow histories must stay compact and deterministic. The child needs enough stable PR identity to wait, resolve, and report a terminal outcome without embedding logs, comments, provider bodies, or large artifacts.
Alternatives considered: Passing full provider responses was rejected because it bloats history and risks secret/provider leakage. Reconstructing publish state after child start was rejected because the child must depend on durable publication evidence.

## Reuse of Existing Merge Gate Code

Decision: Reuse existing merge-gate readiness evaluation and resolver-launch activities where their contracts fit, but expose the source-required child workflow as `MoonMind.MergeAutomation`.
Rationale: Existing MM-341 work already introduced readiness evaluation, blocker records, and resolver launch behavior. MM-350 should not duplicate provider or resolver logic, but it must alter the ownership, workflow type, and terminal completion contract.
Alternatives considered: Writing a separate merge engine was rejected because it recreates provider and resolver behavior. Keeping all existing detached `MoonMind.MergeGate` semantics unchanged was rejected because parent success would remain independent of merge automation success and the workflow name would not match the source requirement.

## Workflow Type Naming

Decision: Use `MoonMind.MergeAutomation` for the parent-owned child workflow and update worker registration/callers directly rather than adding a compatibility alias.
Rationale: The source design names the child workflow and the repository's pre-release compatibility policy rejects hidden aliases for internal contracts. If existing `MoonMind.MergeGate` remains for a separate feature path, MM-350 implementation must make the distinction explicit in code and tests.
Alternatives considered: Reusing `MoonMind.MergeGate` as-is was rejected because it encodes detached completion semantics. Registering both names to the same behavior as an alias was rejected because it hides a workflow contract change.

## Parent Waiting State

Decision: Use existing `awaiting_external` parent state while the child is active, and store compact child workflow identity and current outcome state in parent metadata or summary.
Rationale: The repository already uses `awaiting_external` for durable external waits, and the MM-350 brief requires standard search-attribute paths rather than inventing new root lifecycle vocabulary.
Alternatives considered: Adding a new root terminal or waiting state was rejected because it increases lifecycle surface area without being required for the story.

## Duplicate Prevention

Decision: Derive the child workflow identity from the parent workflow and publish context, and record it before awaiting the child result.
Rationale: Replay, retry, or duplicate publish handling must not start more than one merge automation child for the same PR publication.
Alternatives considered: Relying only on activity-level de-duplication was rejected because child-start idempotency belongs at the workflow boundary.

## Testing Strategy

Decision: Use unit tests for compact model validation and helper payload construction, plus workflow-boundary tests for the real parent invocation shape, child start, await behavior, successful result, non-success result, disabled automation, and duplicate prevention.
Rationale: MM-350 changes Temporal-facing contracts and parent terminal semantics. Repository guidance requires workflow-boundary coverage rather than isolated unit tests only.
Alternatives considered: Testing only readiness activities was rejected because it would miss parent dependency completion behavior.
