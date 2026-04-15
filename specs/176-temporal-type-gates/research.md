# Research: Temporal Type-Safety Gates

## Gate Output Model

Decision: Represent each gate result as a deterministic finding with a rule ID, severity, status, target, message, and remediation.

Rationale: The spec requires every failure to produce an actionable reason. A structured finding model lets unit tests assert exact pass/fail behavior and gives reviewers stable output to interpret.

Alternatives considered: Plain assertion failures in scattered tests. Rejected because they do not provide a shared review-gate contract or consistent remediation details.

## Compatibility Evidence

Decision: Require compatibility-sensitive changes to provide replay or in-flight regression evidence, or explicit versioned cutover notes, before the gate passes.

Rationale: The source design states compatibility outranks model tidiness, and the constitution requires workflow/activity/update/signal payload changes to be safe for in-flight executions or have a cutover plan.

Alternatives considered: Allowing reviewer discretion without evidence. Rejected because it would make gate outcomes non-deterministic and hard to verify.

## Anti-Pattern Checks

Decision: Cover the known anti-patterns with focused repository validation and representative fixtures: raw dictionary activity payloads, public raw dictionary handlers, generic action envelopes, provider-shaped top-level workflow-facing results, unnecessary untyped status leaks, nested raw bytes, and large workflow-history state.

Rationale: The story is a review-gate story, not a full boundary inventory. Representative fixtures can prove each rule catches regressions without expanding scope into every Temporal module.

Alternatives considered: Full static analysis of every possible Python type smell. Rejected as too broad for STORY-005 and likely to create noisy false positives.

## Escape Hatch Policy

Decision: Accept an escape hatch only when it is explicitly marked transitional, bounded to the public boundary, and justified by replay or live in-flight compatibility.

Rationale: The source design permits escape hatches only as transitional mechanisms. The gate should preserve compatibility while preventing the escape hatch from becoming permanent business logic.

Alternatives considered: Reject all `Mapping[str, Any]` and metadata bags. Rejected because the source design explicitly permits bounded compatibility exceptions.

## Test Strategy

Decision: Use targeted unit tests for gate rules and finding output, schema tests for payload/escape-hatch behavior, and workflow-boundary or replay-style tests for compatibility-sensitive cases. Use `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration when Docker is available.

Rationale: The repo’s required test taxonomy separates unit verification from compose-backed integration. This story needs both rule-level determinism and Temporal boundary confidence.

Alternatives considered: Running only static checks. Rejected because the source design requires schema, boundary round-trip, replay/in-flight, and static-analysis coverage.
