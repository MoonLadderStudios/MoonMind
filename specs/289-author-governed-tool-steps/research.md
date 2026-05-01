# Research: Author Governed Tool Steps

## FR-001 / Trusted Tool Discovery

Decision: Use the existing same-origin `/mcp/tools` endpoint as the Create page discovery source and treat failures as non-blocking.
Evidence: `docs/ExternalAgents/ModelContextProtocol.md` documents `GET /mcp/tools`; current Create page has only manual Tool id fields.
Rationale: This preserves the trusted tool boundary and avoids raw Jira credentials or direct provider calls.
Alternatives considered: Hardcoded tool list rejected because it would drift and violate governed discovery.
Test implications: Frontend integration test with mocked discovery response.

## FR-003 / Grouping and Search

Decision: Derive group labels from the namespace before the first dot in the tool name, with a search field filtering by name, description, and group.
Evidence: Trusted tool names such as `jira.get_issue` and `jira.transition_issue` already carry stable namespaces.
Rationale: Provides immediate grouping without waiting for richer catalog metadata.
Alternatives considered: Backend catalog enrichment deferred; not required for this single story.
Test implications: Frontend integration test verifies Jira and GitHub groups and search filtering.

## FR-005 / Dynamic Jira Target Status Options

Decision: For `jira.transition_issue`, parse `issueKey` from Tool inputs JSON and call `/mcp/tools/call` with `jira.get_transitions`; populate target status options from returned transition `to.name` values.
Evidence: Trusted Jira tool surface already exposes `jira.get_transitions`; the story explicitly cites dynamic Jira target statuses.
Rationale: Uses trusted Jira data and avoids guessing transition IDs or statuses.
Alternatives considered: Static status list rejected because Jira workflows vary by issue and permission.
Test implications: Frontend integration test mocks `jira.get_transitions` and verifies JSON update/submission.

## FR-008 / Submission Boundary

Decision: Preserve existing Tool payload submission and backend contract tests for conflicting Skill payload and shell-like fields.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` has governed Tool submission coverage; `tests/unit/workflows/tasks/test_task_contract.py` rejects conflicting payloads and shell-like fields.
Rationale: The new UI is an authoring affordance; the execution contract remains unchanged.
Alternatives considered: New backend validation not needed for this story.
Test implications: Rerun focused unit contract test and frontend submission test.
