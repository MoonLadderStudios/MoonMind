# Research: Jules Question Auto-Answer

## Decision: Question Extraction via Activities API

**Decision**: Use `GET /v1alpha/sessions/{session}/activities` to extract Jules's question text.
**Rationale**: The Jules API `sessions.activities` sub-resource exposes `AgentMessaged` activities with `agentMessage: string`. When Jules is in `AWAITING_USER_FEEDBACK`, the most recent `AgentMessaged` activity from `originator: "agent"` contains the question.
**Alternatives considered**: (1) Parsing session description/URL — no question text available there. (2) Polling a hypothetical messages endpoint — doesn't exist in the Jules API.

## Decision: One-Shot LLM for Default Answer Dispatch

**Decision**: Default to a one-shot LLM activity (`mm.activity.llm.complete` or similar) for generating answers.
**Rationale**: Jules questions are typically factual clarifications (e.g., "should I modify existing tests or create new ones?") that don't require tool use. A fast model call is sufficient and avoids acquiring auth profile slots.
**Alternatives considered**: Full managed agent runtime (`MoonMind.AgentRun` → `gemini_cli`) — too heavyweight for simple clarifications; supported as opt-in via `JULES_AUTO_ANSWER_RUNTIME`.

## Decision: In-Workflow State Tracking

**Decision**: Track answered activity IDs and question count as workflow-level state variables, not database records.
**Rationale**: Each auto-answer cycle is scoped to a single `MoonMind.AgentRun` or `MoonMind.Run` workflow instance. No need for persistent storage. Temporal workflow state survives replays.
**Alternatives considered**: Writing dedup records to the database — unnecessary complexity for a per-run concern.

## Decision: Status Normalization to `awaiting_feedback`

**Decision**: Introduce a new normalized status `awaiting_feedback` distinct from `running`.
**Rationale**: The polling loop must distinguish "Jules is actively working" from "Jules is blocked and needs a response." Mapping both to `running` causes the current stall.
**Alternatives considered**: (1) Using `intervention_requested` directly — too aggressive; the workflow should try to answer first. (2) Using `awaiting_callback` — semantically wrong; the workflow is not waiting for a callback but needs to take action.
