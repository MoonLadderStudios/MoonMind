# Research: Merge Automation Waits

## Canonical Workflow Naming

Decision: Use `MoonMind.MergeAutomation` as the canonical workflow type and remove `MoonMind.MergeGate` from internal workflow type registries and tests.

Rationale: MM-351 and `docs/Tasks/PrMergeAutomation.md` define the workflow type as `MoonMind.MergeAutomation`. The project is pre-release and the compatibility policy requires replacing superseded internal contracts rather than retaining aliases.

Alternatives considered: Keeping `MoonMind.MergeGate` as a compatibility alias was rejected because it would preserve a superseded internal workflow contract and conflict with the canonical source design.

## Compact Start Payload

Decision: Start the workflow with parent ids, `publishContextRef`, `mergeAutomationConfig`, `resolverTemplate`, and only compact PR identity fields needed for deterministic waiting.

Rationale: The workflow needs current PR number, URL, and head SHA to evaluate readiness, but must avoid embedding large publish payloads in workflow history. Compact identity plus artifact-backed ref satisfies both constraints.

Alternatives considered: Loading all publish context into workflow input was rejected because it increases history size. Loading the entire publish context inside workflow code was rejected because external artifact reads belong in activities.

## Wait Strategy

Decision: Prefer external event signals and use configured `fallbackPollSeconds` as the bounded wait timeout before re-evaluation.

Rationale: Temporal workflows can wait deterministically on signals and timers. This avoids fixed-delay resolver launch and keeps readiness state-based.

Alternatives considered: Scheduling a fixed resolver follow-up after a constant delay was rejected by the source design. Provider polling without signals was rejected because it delays readiness when webhook/signal events are available.

## Continue-As-New

Decision: Add a compact Continue-As-New payload builder and trigger Continue-As-New when Temporal suggests it after a waiting cycle.

Rationale: Long-lived waits can accumulate history. Preserving parent ids, publish ref, PR identity, latest head SHA, policy, Jira key, blockers, cycle count, resolver history, and deadline keeps the next run deterministic and compact.

Alternatives considered: Relying only on normal workflow history was rejected because MM-351 explicitly requires Continue-As-New preservation.

## Test Boundaries

Decision: Cover workflow behavior with unit-level Temporal workflow-boundary tests using fake readiness and resolver activities, plus focused model/helper tests.

Rationale: These tests exercise the worker-bound invocation shape and deterministic workflow logic without requiring live GitHub/Jira credentials.

Alternatives considered: Only testing GitHub/Jira service helpers was rejected because the story is about workflow wait semantics, signals, and compact state.
