# Implementation Plan: Gemini 429 Cooldown Retry

**Branch**: `106-gemini-429-cooldown-retry` | **Date**: 2026-03-28 | **Spec**: [`spec.md`](./spec.md)

## Summary

Terminate managed Gemini CLI runs as soon as live output proves the provider is capacity-rate-limited, map that terminal result into provider error code `429`, and let `MoonMind.AgentRun` re-enter the existing profile-slot cooldown path with a retry summary that surfaces in task details.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Temporal Python SDK, FastAPI/Pydantic, existing managed runtime supervisor/log streamer
**Storage**: Managed run store JSON files, local runtime artifacts
**Testing**: `./tools/test_unit.sh`
**Target Platform**: Docker Compose local stack / Temporal workers
**Project Type**: Backend workflow/runtime + Mission Control detail rendering via existing summary field

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Keeps cooldown behavior in workflow orchestration, not UI hacks.
- II. One-Click Agent Deployment: PASS. No new external dependency.
- III. Avoid Vendor Lock-In: PASS. Gemini-specific parsing is isolated to the Gemini strategy/runtime path.
- IV. Own Your Data: PASS. Evidence remains in managed run logs/diagnostics and execution summaries.
- V. Skills Are First-Class and Easy to Add: PASS. No skill contract changes.
- VI. Design for Deletion / Thick Contracts: PASS. Extends existing runtime parser/supervisor contracts instead of adding bespoke side channels.
- VII. Powerful Runtime Configurability: PASS. Uses configurable cooldown values instead of hardcoded retry delay.
- VIII. Modular and Extensible Architecture: PASS. Changes stay in runtime strategy, supervisor, and workflow boundaries.
- IX. Resilient by Default: PASS. Converts a livelock into a bounded cooldown retry with operator-visible reason.
- X. Facilitate Continuous Improvement: PASS. Failure reason becomes visible in task details and diagnostics.
- XI. Spec-Driven Development: PASS. This plan implements the attached spec.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. No migration backlog added to canonical docs.
- XIII. Pre-Release / Compatibility Policy: PASS. No backward-compat wrapper; extends existing internal behavior directly.

## Project Structure

### Documentation

```text
specs/106-gemini-429-cooldown-retry/
├── plan.md
├── spec.md
└── tasks.md
```

### Code

```text
moonmind/workflows/temporal/runtime/
moonmind/workflows/temporal/workflows/
api_service/api/routers/
tests/unit/services/temporal/runtime/
tests/unit/workflows/temporal/
tests/integration/services/temporal/workflows/
```

## Implementation Strategy

1. Add Gemini-specific live output parsing for capacity-exhausted 429 markers and expose stream events during supervision.
2. Terminate the Gemini process early when those markers appear, then classify the run as a managed rate-limit failure using diagnostics-backed enrichment.
3. Cache provider profile cooldown metadata in `MoonMind.AgentRun`, use it for retry delay, and publish a retry-specific `awaiting_slot` summary that the existing task details timeline already displays.
4. Update provider profile create defaults to 900 seconds so new profiles inherit the requested 15-minute retry window by default.

## Testing Strategy

- Add supervisor/runtime tests proving live Gemini 429 output terminates the process and persists rate-limit diagnostics.
- Add workflow-boundary tests proving `MoonMind.AgentRun` reports cooldown, releases/re-requests slot, and emits the retry summary.
- Run targeted unit coverage through `./tools/test_unit.sh`.
