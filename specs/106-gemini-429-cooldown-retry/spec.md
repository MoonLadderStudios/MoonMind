# Feature Specification: Gemini 429 Cooldown Retry

**Feature Branch**: `106-gemini-429-cooldown-retry`
**Created**: 2026-03-28
**Status**: Draft
**Input**: User request: "Gemini 429 capacity failures should show in task details timeline and ideally retry 15 minutes later, configurable as a setting."

## User Scenarios & Testing

### User Story 1 - Capacity exhaustion is visible while the task waits to retry (Priority: P1)

When a managed Gemini CLI run hits a provider 429 capacity error, the task details page should show that the task is waiting because Gemini capacity was exhausted and that a retry is scheduled after cooldown.

**Why this priority**: Operators currently see an apparently stuck task instead of a meaningful waiting state.

**Independent Test**: Trigger a managed Gemini run that emits a recognizable capacity-exhausted 429, then verify the execution enters `awaiting_slot` with a summary that mentions the 429/capacity condition and retry timing.

**Acceptance Scenarios**:

1. **Given** a managed Gemini CLI task is running, **when** the live runtime output contains a Gemini capacity-exhausted 429 marker, **then** the workflow summary changes to an `awaiting_slot` reason that explains the capacity failure and scheduled retry.
2. **Given** the task details UI renders a Temporal execution, **when** the execution is waiting on the Gemini cooldown retry, **then** the timeline/waiting detail shows that summary without requiring raw Temporal history browsing.

---

### User Story 2 - Capacity exhaustion stops the live retry loop and schedules a durable retry (Priority: P1)

When Gemini CLI enters its own internal retry loop after a capacity 429, MoonMind should stop the live run promptly and hand control back to the workflow cooldown path.

**Why this priority**: Without early termination, the workflow polls indefinitely and the built-in cooldown logic never activates.

**Independent Test**: Run supervised Gemini output that emits a capacity-exhausted 429 marker, verify the supervisor terminates the process, the managed run is classified as failed with rate-limit metadata, and `MoonMind.AgentRun` reports cooldown + requests a new slot.

**Acceptance Scenarios**:

1. **Given** a managed Gemini CLI process emits a capacity-exhausted 429 marker while still running, **when** the supervisor observes the live output, **then** it terminates the process instead of waiting for Gemini CLI to keep retrying internally.
2. **Given** the terminated managed run is fetched by `MoonMind.AgentRun`, **when** the workflow inspects the result, **then** it recognizes provider error code `429`, reports cooldown, releases the slot, and re-requests a slot durably.

---

### User Story 3 - Retry delay is configurable through existing cooldown settings (Priority: P2)

Operators should be able to change the retry delay without code changes.

**Why this priority**: Different providers and profiles may require different cooldown windows.

**Independent Test**: Set a non-default cooldown value for the provider profile used by a managed Gemini run, trigger a 429 capacity failure, and verify the scheduled retry summary and manager cooldown use that value.

**Acceptance Scenarios**:

1. **Given** a provider profile has `cooldown_after_429_seconds` configured, **when** Gemini capacity exhaustion occurs on that profile, **then** the workflow uses that configured delay instead of a hardcoded 300 seconds.
2. **Given** a new provider profile is created through the API/UI defaults, **when** the operator does not override the cooldown, **then** the default cooldown value is 900 seconds (15 minutes).

## Requirements

### Functional Requirements

- FR-001: Managed Gemini CLI runtime supervision MUST detect recognizable Gemini capacity-exhausted 429 output while the process is still running.
- FR-002: On detection of a Gemini capacity-exhausted 429, supervision MUST stop the live Gemini process so the workflow can resume deterministic cooldown handling.
- FR-003: Managed run result fetching MUST map the terminated Gemini capacity-exhausted run into provider error code `429`.
- FR-004: `MoonMind.AgentRun` MUST use the configured cooldown duration for the assigned profile when handling managed-provider `429` retries.
- FR-005: `MoonMind.AgentRun` MUST update the parent run summary with a waiting reason that mentions the capacity failure and scheduled retry timing before re-entering slot wait.
- FR-006: The existing task details timeline/waiting surface MUST display that waiting reason without requiring a separate event store.
- FR-007: New provider profiles MUST default `cooldown_after_429_seconds` to 900 seconds unless the operator sets another value.

### Key Entities

- Managed Gemini run record
- Provider profile cooldown configuration
- Parent execution summary / waiting reason

## Success Criteria

### Measurable Outcomes

- SC-001: A Gemini capacity-exhausted 429 no longer leaves the task in an unbounded `agent_runtime.status` poll loop.
- SC-002: During cooldown wait, the task details timeline shows a human-readable reason and retry schedule.
- SC-003: Boundary tests cover live-output detection, managed-run result mapping, and workflow cooldown retry behavior.
