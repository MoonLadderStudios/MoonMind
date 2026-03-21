# Feature Specification: Jules Question Auto-Answer

**Feature Branch**: `094-jules-auto-answer`
**Created**: 2026-03-21
**Status**: Draft
**Input**: User description: "Implement question auto-answer as specified in docs/ExternalAgents/JulesClientAdapter.md"

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
|---|---|---|
| DOC-REQ-001 | JulesClientAdapter.md §9.3 | Status normalizer must map `awaiting_user_feedback` to a distinct `awaiting_feedback` status instead of `running` |
| DOC-REQ-002 | JulesClientAdapter.md §9.4 | System must call the Jules Sessions Activities API (`GET /sessions/{id}/activities`) to extract the latest `AgentMessaged.agentMessage` text when Jules is in `AWAITING_USER_FEEDBACK` state |
| DOC-REQ-003 | JulesClientAdapter.md §9.5 | System must dispatch the extracted question to a managed agent runtime (one-shot LLM by default) to generate a clarification answer |
| DOC-REQ-004 | JulesClientAdapter.md §9.6 | System must send the generated answer back to Jules via `sendMessage` (`POST /sessions/{id}:sendMessage`) |
| DOC-REQ-005 | JulesClientAdapter.md §9.7 | The auto-answer flow must run inline in both `MoonMind.AgentRun` external polling loop and `MoonMind.Run._run_integration_stage()` integration polling loop |
| DOC-REQ-006 | JulesClientAdapter.md §9.8 | System must enforce a configurable max auto-answer cycle limit (default 3), after which status maps to `intervention_requested` |
| DOC-REQ-007 | JulesClientAdapter.md §9.8 | System must deduplicate answered questions using activity IDs to prevent re-answering the same question |
| DOC-REQ-008 | JulesClientAdapter.md §9.8 | System must support an opt-out configuration (`JULES_AUTO_ANSWER_ENABLED`) that maps `AWAITING_USER_FEEDBACK` directly to `intervention_requested` when disabled |
| DOC-REQ-009 | JulesClientAdapter.md §9.9 | System must expose four configuration variables: `JULES_AUTO_ANSWER_ENABLED`, `JULES_MAX_AUTO_ANSWERS`, `JULES_AUTO_ANSWER_RUNTIME`, `JULES_AUTO_ANSWER_TIMEOUT_SECONDS` |
| DOC-REQ-010 | JulesClientAdapter.md §9.10 | System must add Pydantic schema models for Jules activities API: `JulesActivity`, `JulesAgentMessage`, `JulesListActivitiesResult` |
| DOC-REQ-011 | JulesClientAdapter.md §9.4 | System must register a new Temporal activity `integration.jules.list_activities` on the integrations task queue |
| DOC-REQ-012 | JulesClientAdapter.md §9.5 | System must register a new Temporal activity `integration.jules.answer_question` on the integrations task queue |

## User Scenarios & Testing

### User Story 1 — Workflow Automatically Answers Jules Question (Priority: P1)

When Jules encounters an ambiguity during execution and enters `AWAITING_USER_FEEDBACK`, the workflow detects this, extracts the question, generates an answer using the default LLM, and sends the answer back to Jules so the session can continue.

**Why this priority**: This is the core value — without it, every Jules question stalls the workflow indefinitely, requiring manual intervention and wasting operator time.

**Independent Test**: Can be tested by starting a Jules session that triggers `AWAITING_USER_FEEDBACK`, verifying the auto-answer sub-flow triggers, and confirming Jules resumes `IN_PROGRESS`.

**Acceptance Scenarios**:

1. **Given** a Jules session enters `AWAITING_USER_FEEDBACK` during execution, **When** the polling loop detects this status, **Then** the system calls `integration.jules.list_activities`, extracts the latest `AgentMessaged.agentMessage`, dispatches it to the configured LLM, and sends the answer via `sendMessage`.
2. **Given** the auto-answer is sent, **When** Jules receives the message, **Then** the session transitions away from `AWAITING_USER_FEEDBACK` (to `IN_PROGRESS` or another active state) and the polling loop resumes normally.

---

### User Story 2 — Max Auto-Answer Cycles with Escalation (Priority: P1)

If Jules asks more than the configured maximum number of questions (default 3), the system stops auto-answering and escalates to the operator via Mission Control by mapping the status to `intervention_requested`.

**Why this priority**: Prevents infinite question-answer loops and ensures a human can intervene when the automated system cannot resolve Jules's needs within a reasonable number of attempts.

**Independent Test**: Can be tested by configuring `JULES_MAX_AUTO_ANSWERS=1` and triggering two consecutive Jules questions — the second should result in `intervention_requested`.

**Acceptance Scenarios**:

1. **Given** `JULES_MAX_AUTO_ANSWERS` is set to 3 and 3 questions have already been auto-answered in this session, **When** Jules enters `AWAITING_USER_FEEDBACK` a 4th time, **Then** the workflow sets status to `intervention_requested` and does not call the LLM.

---

### User Story 3 — Auto-Answer Disabled (Priority: P2)

When `JULES_AUTO_ANSWER_ENABLED` is set to `false`, any `AWAITING_USER_FEEDBACK` status immediately maps to `intervention_requested` without attempting extraction or LLM dispatch.

**Why this priority**: Provides an operator control to disable the feature entirely if needed, falling back to manual intervention.

**Independent Test**: Can be tested by setting `JULES_AUTO_ANSWER_ENABLED=false` and verifying that `AWAITING_USER_FEEDBACK` maps to `intervention_requested` without calling any activities.

**Acceptance Scenarios**:

1. **Given** `JULES_AUTO_ANSWER_ENABLED=false`, **When** Jules enters `AWAITING_USER_FEEDBACK`, **Then** the status maps to `intervention_requested` and no `list_activities` or `send_message` activities are invoked.

---

### User Story 4 — Question Deduplication (Priority: P2)

If the polling loop detects `AWAITING_USER_FEEDBACK` multiple times for the same question (same activity ID), the system does not re-answer it.

**Why this priority**: Prevents duplicate answers being sent to Jules due to poll timing, which could confuse the session.

**Independent Test**: Can be tested by returning the same `AWAITING_USER_FEEDBACK` status and activity ID across two consecutive polls and verifying only one answer is sent.

**Acceptance Scenarios**:

1. **Given** question with activity ID `abc123` has already been answered, **When** the next poll still shows `AWAITING_USER_FEEDBACK` with the same activity ID, **Then** the system skips the LLM dispatch and waits for Jules to transition.

---

### Edge Cases

- What happens when the `list_activities` API returns no `AgentMessaged` activities? System should log a warning and continue polling (count as unanswered, not toward max limit).
- What happens when the LLM fails to generate an answer? The question counts as unanswered, the cycle is retried on the next poll (up to max cycle limit).
- What happens when `sendMessage` fails after the LLM generates an answer? The system retries with standard retry policy. If retries exhaust, the workflow fails.
- What happens when the auto-answer timeout expires? The cycle counts as exhausted toward the max limit.

## Requirements

### Functional Requirements

- **FR-001**: System MUST map `awaiting_user_feedback` to `awaiting_feedback` (not `running`) in both `jules_models.py` `_JULES_STATUS_MAP` and `jules/status.py` normalizer. (DOC-REQ-001)
- **FR-002**: System MUST add `"awaiting_feedback"` to `JulesNormalizedStatus` literal type. (DOC-REQ-001)
- **FR-003**: System MUST implement `JulesClient.list_activities(session_id)` transport method that calls `GET /v1alpha/sessions/{id}/activities` and returns parsed activities. (DOC-REQ-002)
- **FR-004**: System MUST implement a Temporal activity `integration.jules.list_activities` on the `mm.activity.integrations` queue that calls `JulesClient.list_activities()` and extracts the latest `AgentMessaged.agentMessage`. (DOC-REQ-002, DOC-REQ-011)
- **FR-005**: System MUST implement a Temporal activity `integration.jules.answer_question` on the `mm.activity.integrations` queue that orchestrates the question-answer cycle (extract question → dispatch to LLM → send answer). (DOC-REQ-003, DOC-REQ-012)
- **FR-006**: System MUST add Pydantic models `JulesActivity`, `JulesAgentMessage`, and `JulesListActivitiesResult` to `moonmind/schemas/jules_models.py`. (DOC-REQ-010)
- **FR-007**: System MUST detect `awaiting_feedback` status in `MoonMind.AgentRun` external polling loop and trigger the auto-answer sub-flow. (DOC-REQ-005)
- **FR-008**: System MUST detect `awaiting_feedback` status in `MoonMind.Run._run_integration_stage()` and trigger the auto-answer sub-flow. (DOC-REQ-005)
- **FR-009**: System MUST enforce a configurable maximum auto-answer cycle limit (default 3, `JULES_MAX_AUTO_ANSWERS`). (DOC-REQ-006)
- **FR-010**: System MUST track answered activity IDs to prevent duplicate answers. (DOC-REQ-007)
- **FR-011**: System MUST support `JULES_AUTO_ANSWER_ENABLED` (default `true`) to disable the feature entirely. (DOC-REQ-008)
- **FR-012**: System MUST read config from four env vars: `JULES_AUTO_ANSWER_ENABLED`, `JULES_MAX_AUTO_ANSWERS`, `JULES_AUTO_ANSWER_RUNTIME`, `JULES_AUTO_ANSWER_TIMEOUT_SECONDS`. (DOC-REQ-009)
- **FR-013**: System MUST send the LLM-generated answer back to Jules via the existing `integration.jules.send_message` activity. (DOC-REQ-004)

### Key Entities

- **JulesActivity**: A session activity from the Jules Activities API, representing any activity type (agent message, user message, plan, progress, etc.)
- **JulesAgentMessage**: An `AgentMessaged` activity containing the `agentMessage` text field — the question Jules is asking
- **JulesListActivitiesResult**: Extracted question context including `latest_agent_question` text and `activity_id` for deduplication
- **AutoAnswerState**: Workflow-level state tracking answered activity IDs and question count per session

## Success Criteria

### Measurable Outcomes

- **SC-001**: Workflows with Jules questions that previously stalled indefinitely now complete successfully when auto-answer provides adequate clarification.
- **SC-002**: Auto-answer response is delivered to Jules within 60 seconds of detecting `AWAITING_USER_FEEDBACK` (for default LLM mode).
- **SC-003**: Workflows exceeding the max auto-answer limit correctly escalate to `intervention_requested` within one polling cycle.
- **SC-004**: All unit tests pass covering the question extraction, LLM dispatch, answer delivery, dedup, and max-cycle guardrails.

## Assumptions

- The Jules Activities API returns activities in reverse chronological order (newest first), so the first `AgentMessaged` activity is the latest question.
- Jules transitions out of `AWAITING_USER_FEEDBACK` after receiving a `sendMessage` response within a reasonable time (< 60 seconds).
- The one-shot LLM activity (`mm.activity.llm.complete` or equivalent) is available on the agent runtime task queue.
- The existing `integration.jules.send_message` activity and transport layer are functional and tested.
