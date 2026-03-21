# Implementation Plan: Jules Question Auto-Answer

**Branch**: `094-jules-auto-answer` | **Date**: 2026-03-21 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/094-jules-auto-answer/spec.md`

## Summary

When Jules enters `AWAITING_USER_FEEDBACK`, MoonMind detects the feedback state, extracts the question via the Jules Activities API, dispatches it to a one-shot LLM for an answer, and sends the answer back to Jules via `sendMessage`. This prevents workflow stalls caused by unanswered questions.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, aiohttp (Jules HTTP client)
**Storage**: N/A (workflow-level in-memory state only)
**Testing**: pytest (via `./tools/test_unit.sh`)
**Target Platform**: Linux server (Docker worker containers)
**Project Type**: Single project — modifications to existing `moonmind` package
**Performance Goals**: Auto-answer response within 60 seconds of detecting `AWAITING_USER_FEEDBACK`
**Constraints**: Max 3 auto-answer cycles per session by default; bounded timeouts per cycle
**Scale/Scope**: Affects Jules external agent integration path only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Compliance |
|-----------|------------|
| Orchestrate, don't recreate | ✅ Uses existing `sendMessage` transport, existing LLM activity, and existing Temporal workflow patterns |
| No vendor lock-in | ✅ Question extraction and LLM dispatch are provider-neutral in the workflow layer; only the Jules-specific activity parses activities API |
| Spec-driven development | ✅ Implementing from design doc with DOC-REQ traceability |
| Modular architecture | ✅ New activities are thin wrappers; orchestration logic stays in `MoonMind.AgentRun` |
| No secrets in logs/history | ✅ No new credentials needed; reuses existing `JulesClient` auth |

## Project Structure

### Documentation (this feature)

```text
specs/094-jules-auto-answer/
├── spec.md
├── plan.md                              # This file
├── research.md                          # Phase 0 output
├── data-model.md                        # Phase 1 output
├── quickstart.md                        # Phase 1 output
├── contracts/
│   └── requirements-traceability.md     # DOC-REQ mapping
├── checklists/
│   └── requirements.md                  # Spec quality checklist
└── tasks.md                             # Phase 2 output (speckit-tasks)
```

### Source Code (repository root)

```text
moonmind/schemas/jules_models.py           # New Pydantic models, status map change
moonmind/jules/status.py                   # Status normalization change
moonmind/workflows/adapters/jules_client.py  # New list_activities() transport method
moonmind/workflows/temporal/activities/jules_activities.py  # New activities
moonmind/workflows/temporal/activity_runtime.py  # Activity registration
moonmind/workflows/temporal/activity_catalog.py  # Activity catalog entries
moonmind/workflows/temporal/workflows/agent_run.py  # Auto-answer sub-flow in polling loop
moonmind/workflows/temporal/workflows/run.py  # Auto-answer sub-flow in integration stage

tests/unit/schemas/test_jules_models.py        # Schema model tests
tests/unit/workflows/test_agent_run_auto_answer.py  # Workflow polling logic tests
tests/unit/workflows/test_jules_activities.py  # Activity tests
```

**Structure Decision**: All changes are modifications to existing modules in the `moonmind` package. No new top-level directories or packages needed.

## Implementation Phases

### Phase 1: Schema & Status Normalization (FR-001, FR-002, FR-006)

1. Add `"awaiting_feedback"` to `JulesNormalizedStatus` literal in `jules_models.py` and `jules/status.py`
2. Update `_JULES_STATUS_MAP["awaiting_user_feedback"]` from `"running"` to `"awaiting_feedback"`
3. Add `JulesAgentMessage`, `JulesActivity`, `JulesListActivitiesResult` Pydantic models
4. Add unit tests for new models and status mapping

### Phase 2: Transport Layer (FR-003)

1. Add `JulesClient.list_activities(session_id)` method calling `GET /v1alpha/sessions/{id}/activities`
2. Parse response into `JulesActivity` models
3. Extract latest `AgentMessaged.agentMessage` from activities list
4. Add unit tests for transport method

### Phase 3: Temporal Activities (FR-004, FR-005, DOC-REQ-011, DOC-REQ-012)

1. Register `integration.jules.list_activities` activity in `jules_activities.py`
2. Register `integration.jules.answer_question` activity (orchestrates: list_activities → LLM → send_message)
3. Add activity catalog entries in `activity_catalog.py`
4. Add handler in `activity_runtime.py`
5. Add unit tests for both activities

### Phase 4: Workflow Orchestration (FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013)

1. Add `awaiting_feedback` to `_EXTERNAL_STATUS_TO_RUN_STATUS` in `agent_run.py`
2. Add auto-answer sub-flow in `MoonMind.AgentRun` polling loop (detect → list → LLM → sendMessage). `MoonMind.Run` delegates to `AgentRun` child workflows and does not duplicate this logic.
4. Implement max-cycle enforcement, deduplication, opt-out, and timeout guardrails
5. Read config from env vars: `JULES_AUTO_ANSWER_ENABLED`, `JULES_MAX_AUTO_ANSWERS`, `JULES_AUTO_ANSWER_RUNTIME`, `JULES_AUTO_ANSWER_TIMEOUT_SECONDS`
6. Add unit tests for workflow logic

## Complexity Tracking

No constitution violations to justify.
