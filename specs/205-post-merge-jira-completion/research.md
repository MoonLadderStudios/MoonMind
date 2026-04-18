# Phase 0 Research: Post-Merge Jira Completion

## Input Classification

Decision: Treat MM-403 as one runtime story for Jira-backed merge automation completion.

Evidence: `specs/205-post-merge-jira-completion/spec.md` contains one `## User Story - Complete Jira After Merge` section and preserves the MM-403 Jira preset brief.

Rationale: The story has one independently testable outcome: after verified merge success, complete exactly one authoritative Jira issue or fail/no-op safely.

Alternatives considered: Running `moonspec-breakdown` was rejected because the brief does not require multiple independently testable specs.

Test implications: No tests beyond final traceability verification for classification.

## Existing Merge Automation Lifecycle

Decision: Implement post-merge Jira completion inside `MoonMind.MergeAutomation` after resolver success and before terminal success.

Evidence: `moonmind/workflows/temporal/workflows/merge_automation.py` classifies resolver dispositions and currently returns `_finish()` immediately for `merged` and `already_merged`. `docs/Tasks/PrMergeAutomation.md` states parent success means merge automation succeeded and downstream dependencies wait on the parent task.

Rationale: This is the only point where the system knows the resolver child produced a merge-success disposition and still owns parent success semantics.

Alternatives considered: Putting completion inside `pr-resolver` was rejected by the MM-403 source brief and would move trusted Jira mutation into lower-level resolver execution. Running completion in the parent after child return was rejected because the child owns merge automation evidence and terminal status.

Test implications: Workflow-boundary tests must prove both `merged` and `already_merged` run completion before terminal success, and non-success dispositions do not run completion.

## Trusted Jira Boundary

Decision: Use the existing Jira service/tool layer through an activity-bound integration path for issue fetch, transition discovery, and transition execution.

Evidence: `moonmind/integrations/jira/tool.py` provides `get_issue`, `get_transitions`, and `transition_issue`; `docs/Tools/JiraIntegration.md` requires raw Jira credentials to remain inside trusted backend code paths.

Rationale: This satisfies the source requirement to reuse MoonMind's trusted Jira integration instead of adding direct credentials or raw Jira calls to agent shells.

Alternatives considered: Calling Jira from workflow code directly was rejected because Temporal workflows must remain deterministic and should not perform side effects. Calling Jira from resolver scripts was rejected for credential and ownership reasons.

Test implications: Unit tests should use stubbed service/activity boundaries. Integration-style tests should verify transition lookup and transition failure behavior without raw credentials.

## Jira Issue Resolution

Decision: Add a deterministic post-merge issue resolver that gathers candidate keys from explicit post-merge config, merge automation input, captured task origin/publish context where available, and strict PR metadata fallback.

Evidence: `MergeAutomationStartInput` already carries `jiraIssueKey`, and `MoonMind.Run` builds that payload from merge automation parameters. No helper currently resolves one authoritative post-merge Jira key or handles ambiguity.

Rationale: The feature must not guess. A resolver contract allows the workflow to fail closed on missing, invalid, or ambiguous keys while preserving evidence.

Alternatives considered: Fuzzy Jira summary search was rejected because the spec explicitly excludes it. Transitioning every key found in PR metadata was rejected because MM-403 is scoped to one authoritative issue.

Test implications: Unit tests must cover precedence, deduplication, validation success, missing candidates, invalid candidates, and conflicting validated candidates.

## Transition Selection

Decision: Add a strict completion transition selector that no-ops when the issue is already done, validates explicit transition overrides when provided, otherwise selects exactly one available done-category transition.

Evidence: `JiraToolService.get_transitions()` can fetch available transitions, and `transition_issue()` can reject stale transition IDs when explicit lookup is required. No existing selector chooses a done transition for post-merge completion.

Rationale: The source brief requires safe transition selection and refusing ambiguity. This helper keeps selection testable outside workflow orchestration.

Alternatives considered: Matching by transition name `Done` only was rejected because Jira workflows can use different done-status names. Choosing the first done-category transition was rejected because it would guess when multiple transitions exist.

Test implications: Unit tests must cover already-done no-op, explicit ID/name validation, exactly-one done transition, zero done transitions, multiple done transitions, and required transition fields without defaults.

## Idempotency And Replay Safety

Decision: Store compact post-merge completion decision/evidence in workflow state and artifacts, and treat already-completed or already-done issues as success no-op on repeated evaluation.

Evidence: `MoonMind.MergeAutomation` already writes compact gate, resolver, and summary artifacts and publishes memo/search attributes. No post-merge Jira decision state exists.

Rationale: Jira transition is an external side effect. The workflow must avoid duplicate transitions under retry/replay and make the observed outcome durable for operators.

Alternatives considered: Depending only on Jira's current status was rejected because operators also need evidence of which transition was selected or why the action no-oped. Persisting large Jira issue payloads in workflow history was rejected by Temporal payload constraints.

Test implications: Workflow tests must cover duplicate/retry behavior and summary/artifact evidence. Unit tests should keep evidence bounded and JSON-serializable.

## Requirement Gap Classification

Decision: Current code is partial for merge automation and trusted Jira primitives, but missing for post-merge completion itself.

Evidence: `merge_automation.py` handles resolver states, readiness, artifacts, and summaries. `temporal_models.py` has merge automation config but no post-merge Jira config/status. `JiraToolService` has issue and transition actions but no post-merge completion orchestration.

Rationale: The feature should build on existing primitives but requires new models, helpers, activity execution, workflow wiring, evidence, docs, and tests.

Alternatives considered: Marking trusted Jira primitives as implemented was rejected because the MM-403 requirement is not satisfied until merge automation uses them for completion.

Test implications: TDD should start with model/helper tests, then workflow-boundary tests, then integration/service tests as needed.

## Unit Test Strategy

Decision: Use `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with targeted Python tests during development and full unit suite before finalization.

Evidence: Repository instructions require `./tools/test_unit.sh` for final unit verification and existing tests for merge automation and Jira service are pytest-based.

Rationale: Most logic can be covered hermetically with model/helper/service stubs.

Alternatives considered: Running provider verification tests was rejected because this story must not require live Jira credentials for required verification.

Test implications: Add tests under `tests/unit/workflows/temporal/`, `tests/unit/workflows/temporal/workflows/`, and `tests/unit/integrations/`.

## Integration Test Strategy

Decision: Add workflow-boundary integration-style tests with stubbed activities/child workflows under the unit runner, and use `./tools/test_integration.sh` only for hermetic `integration_ci` coverage if implementation touches compose-backed integration surfaces.

Evidence: Existing Temporal workflow-boundary tests live under `tests/unit/workflows/temporal/workflows/` and use stubs for activity/child workflow behavior. Repo instructions state Docker may not be available in managed agent containers.

Rationale: The highest-risk behavior is workflow/activity invocation shape and terminal outcome propagation, which can be tested without real credentials.

Alternatives considered: Live Jira provider verification was rejected because it is outside required CI and credential-dependent.

Test implications: Quickstart includes targeted unit commands plus optional integration command when Docker is available.
