# Research: Merge Outcome Propagation

## Parent Outcome Mapping

Decision: Map `merged` and `already_merged` to parent success, `blocked`, `failed`, and `expired` to parent failure, and `canceled` to parent cancellation.

Rationale: The MM-353 source brief and `docs/Tasks/PrMergeAutomation.md` sections 10.2, 16, 17, and 18 make this mapping explicit. Success-only dependency satisfaction remains tied to the original parent `MoonMind.Run`, so non-success outcomes must not be represented as success.

Alternatives considered: Treat `canceled` as failure; rejected because source section 17 and the Jira brief require cancellation to remain truthful and not be reported as failure. Treat unknown statuses as failure with a generic reason; accepted only as deterministic non-success behavior, not as a compatibility mapping.

## Cancellation Propagation

Decision: Use Temporal child workflow cancellation semantics for parent-to-merge-automation cancellation and merge-automation-to-resolver cancellation, with summaries that avoid claiming confirmed cleanup when cancellation is best-effort.

Rationale: The existing workflow code already executes child workflows with Temporal cancellation behavior. The source design requires cancellation propagation and truthful cleanup summaries, but does not require a new persistence model.

Alternatives considered: Add a separate cancellation activity or database record; rejected because it expands scope and duplicates Temporal child workflow state.

## Dependency Satisfaction

Decision: Keep downstream `dependsOn` satisfaction based on the original parent workflow terminal success and verify that failed or canceled parent outcomes remain non-satisfying.

Rationale: `docs/Tasks/PrMergeAutomation.md` section 16 says the root parent workflow id remains the only dependency target and downstream relationships stay unchanged. Existing dependency fan-out code already classifies failed and canceled dependencies as non-success; this story should not redirect dependency targets.

Alternatives considered: Expose merge automation child workflow ids as dependency targets; rejected because the source design explicitly forbids changing dependency targets for v1.

## Test Strategy

Decision: Add focused unit tests for parent status mapping and workflow-boundary tests for child completion/cancellation paths, then run targeted tests through `./tools/test_unit.sh`.

Rationale: The story affects Temporal workflow behavior and dependency semantics, so boundary-level tests provide stronger evidence than isolated helper-only tests. Full hermetic integration can be run when Docker compose is available, but the required local managed-agent verification path is unit tests.

Alternatives considered: Only add helper tests; rejected because Constitution principle IX and repo instructions require workflow-boundary coverage for Temporal-facing behavior.
