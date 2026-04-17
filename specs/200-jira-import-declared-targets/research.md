# Research: Jira Import Into Declared Targets

## Runtime Intent

Decision: Treat MM-381 as runtime Create page behavior, not documentation-only work.  
Rationale: The Jira brief names user-visible Create page behavior and points at `docs/UI/CreatePage.md` as source requirements.  
Alternatives considered: Docs-only alignment was rejected because the user explicitly selected runtime mode.

## Scope Classification

Decision: Classify the request as one independently testable story.  
Rationale: The work centers on one actor and one vertical flow: importing Jira text or images into declared Create page targets.  
Alternatives considered: A broad-design breakdown was rejected because the Jira brief already identifies one bounded story and acceptance criteria.

## Jira Boundary

Decision: Keep Jira access behind existing MoonMind browser APIs and runtime configuration.  
Rationale: `docs/UI/CreatePage.md` forbids direct browser-to-Jira calls and the existing Create page already consumes normalized issue details through `/api/jira/...` endpoints.  
Alternatives considered: Direct Atlassian calls were rejected for security, policy, and testability reasons.

## Target Model

Decision: Represent declared targets as preset objective text, preset objective attachments, step text, and step attachments.  
Rationale: These are the four targets named by the source design and they align with existing draft text and attachment state.  
Alternatives considered: A single generic target was rejected because it cannot prove image target mapping or avoid filename-derived attachment meaning.

## Test Strategy

Decision: Use focused Create page Vitest coverage as both unit and integration-style validation, followed by `./tools/test_unit.sh` for final repository verification.  
Rationale: The story is user-facing browser behavior with existing test harnesses that mock API boundaries and inspect submitted payloads.  
Alternatives considered: Docker-backed integration was rejected because this story does not change backend services or persistence contracts.
