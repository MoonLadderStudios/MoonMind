# Research: Proposal Review Delivery

## Setup Script

Decision: Proceed with manual planning artifacts in the active feature directory.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch name is `change-jira-issue-mm-598-to-status-in-pr-dd08a68f`, not `001-feature-name`; `.specify/feature.json` points at `specs/312-proposal-review-delivery`.
Rationale: The active spec is valid and downstream artifacts can be created deterministically despite the helper's branch-name guard.
Alternatives considered: Renaming the branch was rejected because this managed step is limited to planning artifacts and should not mutate branch state.
Test implications: None beyond final artifact verification.

## FR-001 / External Issue Create-Or-Update

Decision: Add proposal delivery orchestration that performs local dedup lookup, then provider-specific GitHub/Jira issue create-or-update.
Evidence: `TaskProposalService.create_proposal()` persists or reuses local delivery records, but no proposal-specific provider issue creation adapter was found.
Rationale: Delivery records alone do not satisfy reviewer-facing GitHub/Jira delivery.
Alternatives considered: Keeping proposals only in `/api/proposals` was rejected because the MM-598 story requires external trackers as the review surfaces.
Test implications: Unit tests for delivery orchestration and provider adapter calls; boundary/integration tests for submit-to-record-to-delivery flow.

## FR-002 / Delivery Record Shape

Decision: Reuse the existing `task_proposals` delivery-record fields.
Evidence: `TaskProposal` includes provider, external key/url, delivered/synced timestamps, snapshot ref, provider metadata, resolved policy, dedup fields, origin, and status; route tests serialize these fields.
Rationale: The record shape already matches the desired audit/idempotency role closely enough for this story.
Alternatives considered: A new delivery table was rejected unless implementation proves provider decision audit cannot fit the existing record/metadata fields.
Test implications: Final verification plus regression tests when delivery updates these fields.

## FR-003 / Dedup Identity

Decision: Preserve repository-aware normalized-title dedup identity.
Evidence: `TaskProposalService._compute_dedup_fields()` hashes `repository:slugified-title`; unit tests verify repository-aware and title-normalized behavior.
Rationale: This matches source section 5.2 and avoids adding unrelated fields to the dedup identity.
Alternatives considered: Including provider-specific issue type or category in the hash was rejected because it would reduce dedup effectiveness for the same review item.
Test implications: Final verification plus duplicate delivery tests.

## FR-004 / Provider Duplicate Update Path

Decision: Extend duplicate handling beyond local records to provider issue metadata and update/link/comment behavior.
Evidence: `find_open_duplicate()` and duplicate metadata merge exist locally; provider issue metadata search/update was not found.
Rationale: A repeated proposal must not create duplicate reviewer-facing provider issues even when the local record is stale or provider metadata is authoritative.
Alternatives considered: Relying only on local records was rejected because source section 5.2 explicitly includes provider-specific issue metadata.
Test implications: Unit tests with mocked provider lookup/update and stale-local-record cases.

## FR-005 / GitHub Proposal Issue Rendering

Decision: Add a GitHub renderer and trusted issue adapter for `[MoonMind proposal]` issues.
Evidence: `GitHubService` currently covers pull-request operations and issue-read permission evidence, but no proposal issue create/update method or renderer was found.
Rationale: GitHub Issues are a required review surface with labels, hidden marker, evidence links, and reviewer instructions.
Alternatives considered: Encoding proposal details in PR comments was rejected because the spec requires GitHub Issues.
Test implications: Unit tests for Markdown body, labels, hidden marker, dedup marker, action instructions, and sanitized link rendering.

## FR-006 / Jira Proposal Issue Rendering

Decision: Add a Jira renderer that uses trusted Jira tool service primitives for create/update/search.
Evidence: `JiraToolService` provides create, edit, search, transitions, comments, project/action policy, and ADF helpers; no proposal-specific Jira delivery adapter was found.
Rationale: Jira delivery should reuse trusted Jira boundaries rather than raw REST or shell credentials.
Alternatives considered: Calling Jira MCP tools from agent runtime was rejected for product implementation because delivery belongs in trusted control-plane/provider code.
Test implications: Unit tests for ADF description, configured labels/fields, project/type metadata, stored-snapshot notice, and sanitized errors.

## FR-007 / Stored-Snapshot Notice

Decision: Include an explicit stored-snapshot notice in every rendered external issue.
Evidence: Existing records can carry `task_snapshot_ref`, but no issue renderer currently emits a notice.
Rationale: Reviewers must understand that issue text is a review artifact, not executable payload.
Alternatives considered: Relying on docs alone was rejected because the notice must be visible at review time.
Test implications: Renderer tests for GitHub Markdown and Jira ADF.

## FR-008 / Snapshot-Only Promotion Safety

Decision: Preserve existing promotion behavior and add provider decision parsing that only accepts bounded controls.
Evidence: `promote_proposal()` uses stored `task_create_request`; API tests reject `taskCreateRequest` override on promotion. No provider command/event parser exists.
Rationale: The strongest existing safety property should be extended to external tracker actions.
Alternatives considered: Parsing full edited issue body into task payload was rejected by the source design and spec.
Test implications: Unit tests proving edited issue content cannot replace the stored snapshot.

## FR-009 / Reviewer Action Controls

Decision: Implement explicit promote, dismiss, defer, and priority controls per provider.
Evidence: Service supports promote, dismiss, and priority update API paths; no provider-originated command/workflow-state handler was found, and deferral is not end-to-end.
Rationale: External trackers are the review surfaces, so reviewer actions must be available there.
Alternatives considered: Keeping all actions in MoonMind API/UI was rejected because reviewers should not need a dedicated MoonMind proposal queue.
Test implications: Command/state parsing tests and provider decision boundary tests.

## FR-010 / Provider Decision Audit

Decision: Add normalized provider decision event metadata with actor, provider event identity, decision, note/reason, timestamp, and resulting provider state.
Evidence: `promote_proposal()` and `dismiss_proposal()` record MoonMind user decisions; provider event identity and deferred decisions are missing.
Rationale: Provider webhooks and polling can repeat or arrive out of order, so decisions need stable audit and idempotency metadata.
Alternatives considered: Treating provider events as anonymous service calls was rejected because source section 11 requires identity and event idempotency.
Test implications: Unit/boundary tests for duplicate provider event no-op and decision metadata persistence.

## FR-011 / Destination And Action Policy

Decision: Enforce delivery destination and action policy before provider calls.
Evidence: Jira tool service enforces Jira project/action policy; GitHub service resolves permissions for PR/readiness paths; proposal delivery has no dedicated allowlist gate yet.
Rationale: The proposal delivery adapter is the correct place to stop blocked repositories, organizations, Jira sites/projects, and actions before side effects.
Alternatives considered: Letting provider APIs reject unauthorized calls was rejected because operator-visible failures must be policy-aware and sanitized.
Test implications: Unit tests for blocked GitHub repository/org, blocked Jira project/site, and blocked action outcomes.

## FR-012 / Credential Boundary And Redaction

Decision: Keep credential resolution inside trusted provider services and scrub all persisted/output text.
Evidence: Proposal service uses `SecretRedactor`; Jira and GitHub services resolve credentials internally. Proposal delivery adapter does not exist yet.
Rationale: External tracker delivery must not leak provider credentials to managed agents, issue text, logs, comments, or API responses.
Alternatives considered: Passing raw provider tokens through proposal activity inputs was rejected by security guardrails.
Test implications: Redaction tests for provider metadata, errors, rendered body, and logs where practical.

## FR-013 / Sanitized Provider Errors

Decision: Normalize provider errors into sanitized delivery outcomes with affected destination and recoverable next action.
Evidence: Jira/GitHub services already sanitize several low-level errors; proposal delivery needs a story-specific error contract.
Rationale: Operators need actionable status without secrets or raw provider dumps.
Alternatives considered: Returning raw exceptions from provider clients was rejected by the spec and security rules.
Test implications: Unit tests for Jira/GitHub permission failure, validation failure, transient failure, and policy denial summaries.

## FR-014 / Evidence References

Decision: Render evidence refs and artifact/snapshot links by reference only.
Evidence: `task_snapshot_ref`, `external_url`, and origin metadata fields exist; no renderer links evidence yet.
Rationale: Large logs and sensitive artifacts must stay behind artifact refs and operator-controlled access.
Alternatives considered: Embedding full logs or snapshots in external issues was rejected by source section 5.5 and security constraints.
Test implications: Renderer tests confirm links/refs are present and large raw payloads are absent.

## FR-015 / Run Detail And Finish Summary Visibility

Decision: Add or verify existing task run detail and finish summary surfaces expose proposal delivery status and external links.
Evidence: `/api/proposals` serializes external fields; no direct evidence was found in task run details or finish summaries for delivered proposal links.
Rationale: Operators should see external tracker delivery without switching to a dedicated proposal queue.
Alternatives considered: Adding a new proposal page was rejected because the desired system avoids a primary MoonMind proposal queue.
Test implications: API/router or workflow summary tests for delivery status and external URL projection.

## Unit Test Strategy

Decision: Use pytest unit tests for renderers, delivery orchestration, provider adapter decisions, redaction, and service/repository behavior.
Evidence: Existing proposal tests live in `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/workflows/temporal/test_proposal_activities.py`, and `tests/unit/api/routers/test_task_proposals.py`.
Rationale: The highest-risk behavior can be validated without live provider credentials.
Alternatives considered: Provider verification tests were rejected for required coverage because this story should remain hermetic.
Test implications: Run focused pytest during implementation, then `./tools/test_unit.sh`.

## Integration / Boundary Test Strategy

Decision: Add boundary-style tests for proposal submit-to-delivery flow and persisted record updates; use `integration_ci` only if compose-backed DB/API coverage is necessary.
Evidence: The repo has hermetic integration taxonomy and existing proposal activity/service boundaries.
Rationale: Provider delivery touches activity/service/repository/API contracts where mocks alone can miss shape drift.
Alternatives considered: Testing only renderer helpers was rejected because delivery records and provider metadata must persist consistently.
Test implications: Run `./tools/test_integration.sh` if integration_ci coverage is added; otherwise document why unit boundary tests are sufficient.
