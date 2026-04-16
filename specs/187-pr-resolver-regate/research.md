# Research: PR Resolver Child Re-Gating

## Input Classification

Decision: Treat the MM-352 Jira preset brief as a single-story runtime feature request.
Rationale: The brief names one story, STORY-003, and gives one independently testable outcome: resolver attempts run as child MoonMind.Run executions and return dispositions that drive merge, re-gating, or manual-review failure paths.
Alternatives considered: Treating `docs/Tasks/PrMergeAutomation.md` as a broad design was rejected because MM-352 already selects specific source sections and coverage IDs for a single story.

## Resolver Execution Boundary

Decision: Launch resolver attempts as child `MoonMind.Run` workflows using the existing pr-resolver skill substrate.
Rationale: The source design explicitly requires reuse of workspace setup, artifacts, logs, and skill routing. A child `MoonMind.Run` also preserves the product boundary between merge orchestration and skill execution.
Alternatives considered: Direct skill execution inside `MoonMind.MergeAutomation` was rejected because it would duplicate runtime setup and bypass standard run-level observability.

## Resolver Disposition Contract

Decision: Consume a compact `mergeAutomationDisposition` value with the closed set `merged`, `already_merged`, `reenter_gate`, `manual_review`, and `failed`.
Rationale: A closed explicit contract prevents workflow decisions from depending on free-form resolver logs and supports deterministic failure for unknown values.
Alternatives considered: Inferring behavior from child status, diagnostics text, or artifact summaries was rejected as too brittle for workflow replay and operator-visible automation.

## Re-Gating Semantics

Decision: Treat `reenter_gate` as a non-terminal outcome that updates the tracked head SHA when supplied and returns to awaiting external readiness.
Rationale: Resolver-generated pushes can invalidate previous review/check signals. Re-gating ensures merge automation waits for readiness on the current head rather than trusting stale signals.
Alternatives considered: Allowing the resolver child to decide final merge timing after a push was rejected because it violates the shared gate freshness rules.

## Test Strategy

Decision: Use focused unit tests for resolver child request construction and workflow-boundary tests for disposition handling and re-gating behavior.
Rationale: The risk is at the Temporal workflow boundary and payload contract, not in isolated data transformations alone.
Alternatives considered: Provider verification tests were rejected for required validation because this story can be proven hermetically with stub gate and resolver child responses.
