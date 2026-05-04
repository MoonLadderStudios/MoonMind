# Research: GitHub Token Permission Improvements

## FR-001 / DESIGN-REQ-001: Canonical GitHub Credential Resolver

Decision: Partial current support; planned work is to add one resolver used by GitHub API, indexer, publish, and runtime materialization paths.
Evidence: `moonmind/workflows/adapters/github_service.py` has `resolve_github_token()` for explicit token, `GITHUB_TOKEN`, and `settings.github.github_token_secret_ref`; `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` has separate launch resolution; `api_service/services/settings_catalog.py` exposes `integrations.github.token_ref`; `moonmind/config/settings.py` does not include `MOONMIND_GITHUB_TOKEN_REF` in `GitHubSettings.github_token_secret_ref`.
Rationale: Fine-grained PATs fail predictably when the configured secret reference and the runtime caller use different resolution paths. One shared resolver gives deterministic precedence and makes diagnostics consistent.
Alternatives considered: Keep per-service resolvers and add aliases independently. Rejected because it preserves drift and weakens tests.
Test implications: Unit tests for precedence and secret-ref resolution; integration/boundary tests for representative consumers.

## FR-002 / SC-001: Credential Source Precedence

Decision: Missing as specified; implement exact precedence from the spec.
Evidence: Existing tests in `tests/unit/workflows/adapters/test_github_service.py` cover `GITHUB_TOKEN` and configured secret ref; tests in `tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py` cover launch-specific behavior. No tests cover `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, or `MOONMIND_GITHUB_TOKEN_REF` together.
Rationale: The Settings catalog already advertises a token ref; runtime GitHub callers must honor it after direct token and direct env sources.
Alternatives considered: Treat `MOONMIND_GITHUB_TOKEN_REF` as only a Settings API concern. Rejected because the feature goal is runtime use of Settings-configured credentials.
Test implications: Unit tests only for precedence; integration tests should verify at least one consumer path uses the resolver.

## FR-003 / FR-007 / SC-003: Redaction-Safe Source and Token Diagnostics

Decision: Partial current redaction; add token-specific source diagnostics and failure redaction.
Evidence: `moonmind/publish/sanitization.py` and publish tests sanitize PR titles/bodies; `moonmind/utils/logging.py` scrubs GitHub token-like values; current GitHub service summaries do not report selected source category and publish token command output is not covered.
Rationale: Operators need to know whether a token came from explicit input, env, secret ref, or Settings ref, but raw token values must never leak.
Alternatives considered: Hide source category completely. Rejected because troubleshooting fine-grained PAT wiring requires non-secret source evidence.
Test implications: Unit redaction tests for resolver, publish failures, GitHub API diagnostics, and token probe results.

## FR-004 / FR-005 / FR-006 / DESIGN-REQ-002: Token-Aware Publish

Decision: Missing in `PublishService`; planned work is explicit credential injection for push and PR creation.
Evidence: `moonmind/publish/service.py` runs `git push -u origin <branch>` and `gh pr create` without token env or credential helper. `docs/Tasks/TaskPublishing.md` says infrastructure owns deterministic git push and workflow calls `repo.create_pr` in newer publish semantics. Temporal runtime paths set `GIT_TERMINAL_PROMPT=0` in some host push paths, but `PublishService` does not.
Rationale: Fine-grained PATs configured in MoonMind settings are not automatically available to host credential managers or `gh auth`.
Alternatives considered: Require operators to run `gh auth setup-git` on workers. Rejected because it breaks one-click deployment and hides auth source.
Test implications: Unit tests for command env/redaction; integration or handler boundary tests for publish mode branch and PR.

## FR-008 / FR-009 / FR-010 / DESIGN-REQ-003: Fine-Grained Permission Profiles

Decision: Missing; add named permission profile definitions.
Evidence: No code or docs define indexing, publishing, workflow-file, or readiness permission profile objects. Existing docs mention GitHub token references generally.
Rationale: Fine-grained PATs are repository and permission scoped, so validation and guidance must be profile-based rather than classic-scope based.
Alternatives considered: Hardcode permission prose only in docs. Rejected because token probe and diagnostics need structured profile data.
Test implications: Unit tests for profile contents and mode mapping; docs review for operator wording.

## FR-011 / FR-012 / SC-004: Optional Readiness Evidence Degradation

Decision: Partial current readiness behavior; update 403 handling for optional evidence.
Evidence: `GitHubService.evaluate_pull_request_readiness()` calls pull request state, commit status, check runs, reviews, and issue reactions. `_evaluate_github_checks()` and `_evaluate_codex_review_reaction()` currently return generic `external_state_unavailable` blockers for HTTP errors.
Rationale: A token can be valid for publishing but lack optional checks or issue read permissions; readiness should report exactly which evidence is unavailable.
Alternatives considered: Treat any GitHub 403 as fatal. Rejected because optional evidence is policy-dependent and the spec requires graceful degradation.
Test implications: Unit tests for checks 403 and issue reactions 403; one boundary/integration test for readiness result shape.

## FR-013 / FR-014 / SC-005: GitHub Permission Diagnostics

Decision: Partial current support; add sanitized provider diagnostic extraction.
Evidence: `GitHubService.create_pull_request()`, merge, and update-base log response text but return generic summaries such as `GitHub create PR failed with HTTP 403`. No accepted-permissions header extraction was found.
Rationale: Fine-grained PAT failures often include actionable provider metadata that should be shown without leaking secrets.
Alternatives considered: Continue relying on logs. Rejected because operators need the diagnostic in workflow output and logs may be unavailable or too broad.
Test implications: Unit tests for status, message, documentation URL, accepted permissions header, and redaction.

## FR-015 / FR-016 / SC-006: Targeted Token Probe

Decision: Missing; add a bounded token probe for selected `owner/repo` and validation mode.
Evidence: No token probe service, API, or tool contract found. Existing settings readiness focuses on SecretRef resolvability rather than repository permission probing.
Rationale: Fine-grained PAT validation must target the selected repository; global repo listing and classic scopes are not reliable.
Alternatives considered: Validate only by resolving the secret reference. Rejected because a resolvable token can still target the wrong owner/repo or lack endpoint permissions.
Test implications: Unit tests with mocked GitHub responses and a service/API boundary test using no live credentials.

## FR-017 / SC-007: Operator Guidance

Decision: Partial docs support; add desired-state fine-grained PAT and GitHub App guidance.
Evidence: `docs/Security/SettingsSystem.md` documents `integrations.github.token_ref`; `docs/ManagedAgents/ManagedAgentsGit.md` mentions GitHub auth and historical `gh auth` setup; `docs/Tasks/TaskPublishing.md` describes publish semantics. None found document fine-grained PAT limitations in the requested profile language.
Rationale: Some failures are external to MoonMind, such as wrong resource owner or pending org approval, so docs must set operator expectations.
Alternatives considered: Put all guidance in feature artifacts. Rejected because long-lived operator behavior belongs in canonical docs under Constitution XII.
Test implications: Docs review and no code tests beyond checking links/contract consistency.

## FR-018: Coverage Strategy

Decision: Missing for this story; add both unit and boundary coverage.
Evidence: Existing tests cover current GitHub service PR/readiness behavior, managed launch auth, settings catalog SecretRef behavior, and publish metadata sanitization, but not the new combined credential and fine-grained permission contract.
Rationale: This crosses service, adapter, and runtime boundaries; isolated tests alone would miss drift.
Alternatives considered: Provider verification with live GitHub credentials. Rejected for required CI because it needs external credentials; can be optional later.
Test implications: Required unit tests plus hermetic integration/boundary tests; provider verification remains manual/nightly if added.
