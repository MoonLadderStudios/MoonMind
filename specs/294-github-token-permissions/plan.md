# Implementation Plan: GitHub Token Permission Improvements

**Branch**: `294-github-token-permissions` | **Date**: 2026-05-04 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/294-github-token-permissions/spec.md`

## Summary

Implement one explicit GitHub credential path for GitHub API calls, repository indexing, publish push/PR creation, and managed runtime materialization so fine-grained personal access tokens behave predictably for a selected repository. The technical approach is to centralize token resolution, pass resolved credentials into publish and GitHub REST paths without relying on ambient machine auth, add repository-specific permission diagnostics and token probe output, and document fine-grained PAT profiles in desired-state operator docs. Unit tests cover resolver precedence, publish injection, sanitized diagnostics, permission-profile classification, and readiness degradation; hermetic integration tests cover the service/workflow boundaries that submit publish/readiness/probe behavior without live GitHub credentials.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | partial | `api_service/services/settings_catalog.py` exposes `integrations.github.token_ref`; `moonmind/workflows/adapters/github_service.py` resolves explicit token, `GITHUB_TOKEN`, and `github_token_secret_ref`; `moonmind/config/settings.py` lacks `MOONMIND_GITHUB_TOKEN_REF` alias on `GitHubSettings.github_token_secret_ref`. | Add canonical resolver and update all GitHub credential consumers. | unit + integration |
| DESIGN-REQ-002 | missing | `moonmind/publish/service.py` runs `git push` and `gh pr create` without token-aware env or REST PR creation. | Inject resolved credential into push and replace or wrap PR creation with explicit credential use. | unit + integration |
| DESIGN-REQ-003 | missing | No repository-specific fine-grained permission profile catalog or validation output found. | Add permission profile definitions and operator-facing validation output. | unit |
| DESIGN-REQ-004 | partial | Readiness evaluates checks, reviews, and issue reactions in `GitHubService`, but 403s become generic blockers. | Classify optional missing `Checks: read` and `Issues: read` as unavailable evidence with remediation. | unit + integration |
| DESIGN-REQ-005 | partial | GitHub service logs response bodies but result summaries only expose generic HTTP status. | Add sanitized diagnostic extraction for status, message, documentation URL, and accepted permissions header. | unit |
| DESIGN-REQ-006 | missing | No targeted token probe path found for exact `owner/repo` and mode-specific checks. | Add token probe service/API or tool contract backed by exact repository requests. | unit + integration |
| DESIGN-REQ-007 | partial | General secrets/settings docs mention GitHub PAT and token refs, but no fine-grained PAT limitations or GitHub App decision guidance was found. | Update canonical docs with desired-state profiles and limitation guidance. | docs review |
| FR-001 | partial | `GitHubService.resolve_github_token()` and managed runtime launch helpers use separate resolver logic. | Introduce a shared GitHub credential resolver and migrate callers. | unit + integration |
| FR-002 | partial | Direct `GITHUB_TOKEN` and secret refs exist; `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, and Settings token ref precedence are not unified. | Implement exact precedence from spec and remove divergent resolution behavior. | unit |
| FR-003 | missing | Existing errors do not report selected source category consistently. | Return redaction-safe credential source diagnostics. | unit |
| FR-004 | partial | Temporal runtime push paths set `GIT_TERMINAL_PROMPT=0`; `PublishService` branch push does not. | Make publish branch push token-aware and non-interactive. | unit + integration |
| FR-005 | partial | `GitHubService.create_pull_request()` exists; `PublishService` still invokes `gh pr create`. | Route publish PR creation through REST service or explicit `gh` token env. | unit + integration |
| FR-006 | missing | No `GH_TOKEN`/`GITHUB_TOKEN` subprocess env injection in `PublishService`. | If `gh` remains, pass token and repo env explicitly with redaction. | unit |
| FR-007 | partial | Publish title/body sanitization exists; token redaction for push/PR credential output is not covered. | Add token redaction values and secret-like redaction to publish failures. | unit |
| FR-008 | missing | No indexing permission profile definition found. | Add indexing profile requiring selected repo and contents read. | unit |
| FR-009 | missing | No publish permission profile definition found. | Add branch/PR publish profile requiring contents and pull requests write, plus workflow write when needed. | unit |
| FR-010 | missing | No readiness permission profile definition found. | Add readiness profile for pull request, commit status, checks, and issue read. | unit |
| FR-011 | partial | Readiness can continue across successful evidence but blocks on generic optional evidence HTTP errors. | Distinguish required evidence failures from optional unavailable evidence. | unit + integration |
| FR-012 | missing | No missing-permission-specific readiness note found. | Add actionable unavailable-evidence notes naming missing permission and evidence source. | unit |
| FR-013 | partial | HTTP status is included in summaries; sanitized provider message is not. | Extract and surface sanitized GitHub `message`. | unit |
| FR-014 | missing | No documentation URL or accepted permissions header surfaced. | Include sanitized `documentation_url` and `X-Accepted-GitHub-Permissions` where present. | unit |
| FR-015 | missing | No token probe found. | Add targeted probe for selected repository and validation mode. | unit + integration |
| FR-016 | missing | No publish-mode preflight checklist found. | Return publish checklist including required and optional permissions. | unit |
| FR-017 | partial | `docs/Security/SettingsSystem.md` documents GitHub token ref but not fine-grained PAT limitations. | Document unsupported PAT cases and GitHub App recommendation. | docs review |
| FR-018 | missing | Existing tests cover pieces but not the new resolver/publish/diagnostic/probe contract. | Add focused unit and hermetic integration tests. | unit + integration |
| SCN-001 | partial | Some GitHub consumers resolve tokens, but not through one precedence model. | Add end-to-end service-boundary coverage for canonical resolution. | integration |
| SCN-002 | missing | `PublishService` depends on ambient push/`gh` auth. | Add publish branch/PR path using explicit resolved token. | integration |
| SCN-003 | partial | REST failures return HTTP status only. | Add provider diagnostic extraction and tests. | unit |
| SCN-004 | partial | Readiness evaluates checks/reactions but treats permission errors generically. | Add optional permission degradation. | unit + integration |
| SCN-005 | missing | No targeted token probe exists. | Add token probe contract and implementation. | unit + integration |
| SCN-006 | partial | Existing docs mention token refs but not minimum profiles/limitations. | Update docs with profile matrix and non-fixable cases. | docs review |
| SC-001 | missing | No canonical resolver precedence tests for all required source categories. | Add resolver precedence tests. | unit |
| SC-002 | missing | No publish tests prove explicit credential use without ambient auth. | Add publish tests with ambient auth absent. | unit + integration |
| SC-003 | partial | Secret-like publish metadata tests exist; token-specific publish/API/probe failure redaction is absent. | Add redaction tests for token output. | unit |
| SC-004 | missing | No checks/issues permission degradation tests found. | Add readiness 403 permission tests. | unit |
| SC-005 | missing | No accepted-permissions diagnostic tests found. | Add GitHub failure diagnostic tests. | unit |
| SC-006 | missing | No token probe tests found. | Add targeted probe tests. | unit + integration |
| SC-007 | missing | No fine-grained PAT guidance review evidence found. | Add doc guidance and checklist review. | docs review |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, FastAPI, Temporal Python SDK, SQLAlchemy async ORM, `httpx`, PyGithub/LlamaIndex GitHub reader, existing managed secret resolver and publish services  
**Storage**: Existing settings, managed secret, workflow history, and artifact stores only; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` with focused `pytest` targets during iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage; focused service/workflow-boundary pytest tests where no Docker-backed integration is needed  
**Target Platform**: Linux containers and local Docker Compose deployment  
**Project Type**: backend service, Temporal workflow/activity runtime, managed runtime adapters, and canonical operator documentation  
**Performance Goals**: Credential resolution and token probes should add at most one bounded provider request per validation target and should not slow normal publish/readiness paths beyond existing GitHub API latency  
**Constraints**: No raw tokens in logs, artifacts, workflow history, PR text, or persisted run metadata; no reliance on ambient `git`/`gh` credentials for publish; no new external service dependency; fail fast for unsupported credential values; preserve Temporal activity invocation compatibility or document cutover if signatures change  
**Scale/Scope**: One GitHub repository target per token probe; existing GitHub indexing, publish, readiness, and managed runtime materialization flows

**Temporal Cutover Note**: This story keeps existing workflow/activity payload shapes intact. Repository context is read from the existing canonical payload and publish credentials are materialized inside existing activity/worker boundaries, so in-flight Temporal runs do not require a schema migration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. GitHub-specific behavior stays behind GitHub adapter/service boundaries and managed runtime materialization boundaries.
- II. One-Click Agent Deployment: PASS. No new required service or deployment dependency; GitHub remains an optional configured integration.
- III. Avoid Vendor Lock-In: PASS. The feature is GitHub-specific by nature and isolated behind integration contracts; other provider support would add separate adapters.
- IV. Own Your Data: PASS. Diagnostics and probe results remain local, redaction-safe MoonMind outputs.
- V. Skills Are First-Class and Easy to Add: PASS. Skill workflows consume clearer GitHub auth behavior without requiring skill source mutation.
- VI. Thin Scaffolding, Thick Contracts: PASS. The plan strengthens explicit credential and diagnostic contracts instead of inferring from ambient state.
- VII. Runtime Configurability: PASS. Resolution uses existing operator-configured env/settings/secret references with deterministic precedence.
- VIII. Modular and Extensible Architecture: PASS. Planned edits stay in resolver, GitHub adapter, indexer, publish, runtime materialization, and docs surfaces.
- IX. Resilient by Default: PASS. Missing permissions become actionable terminal or degraded evidence states; external side effects remain explicit and retry-safe.
- X. Facilitate Continuous Improvement: PASS. Failure summaries and validation results become structured and operator-actionable.
- XI. Spec-Driven Development: PASS. This plan derives from one `spec.md` story and preserves traceability.
- XII. Canonical Documentation: PASS. Desired-state token profiles belong in canonical docs; rollout and task detail remain in this feature directory.
- XIII. Pre-Release Compatibility Policy: PASS. Internal divergent resolver paths should be removed rather than retained as aliases; Temporal-facing shape changes require boundary tests or explicit cutover notes.

## Project Structure

### Documentation (this feature)

```text
specs/294-github-token-permissions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── github-token-permission-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── auth/
│   └── github_credentials.py
├── config/settings.py
├── indexers/github_indexer.py
├── publish/
│   ├── sanitization.py
│   └── service.py
├── workflows/
│   ├── adapters/github_service.py
│   └── temporal/runtime/
│       ├── github_auth_broker.py
│       ├── launcher.py
│       └── managed_api_key_resolve.py
└── workflows/temporal/activity_runtime.py

api_service/
├── api/
│   ├── routers/settings.py
│   └── schemas.py
└── services/settings_catalog.py

docs/
├── ManagedAgents/ManagedAgentsGit.md
├── Security/SettingsSystem.md
└── Tasks/TaskPublishing.md

tests/
├── unit/
│   ├── auth/test_github_credentials.py
│   ├── publish/test_publish_service_github_auth.py
│   ├── indexers/test_github_indexer.py
│   ├── agents/codex_worker/test_handlers.py
│   ├── services/temporal/runtime/
│   ├── workflows/adapters/test_github_service.py
│   └── workflows/temporal/runtime/test_managed_api_key_resolve.py
└── integration/
    ├── api/test_github_token_probe.py
    └── temporal/test_github_publish_readiness_boundaries.py
```

**Structure Decision**: Use the existing Python backend, Temporal runtime, GitHub adapter, settings catalog, publish service, and docs structure. No new project or persistent storage layer is required.

## Phase 0 Research Summary

See [research.md](./research.md). The current repo has partial GitHub token support in several separate places, but no single canonical resolver, no token-aware `PublishService` push/PR path, no fine-grained permission profile/probe contract, and only generic GitHub permission failure summaries. Existing tests cover current behavior and should be extended rather than replaced.

## Phase 1 Design Summary

See [data-model.md](./data-model.md), [quickstart.md](./quickstart.md), and [github-token-permission-contract.md](./contracts/github-token-permission-contract.md). The planned design adds compact redaction-safe data contracts for credential source selection, permission profiles, provider diagnostics, token probe output, and readiness evidence availability. Public surfaces are the GitHub service contract, publish behavior, token probe API/tool output, and canonical operator docs.

## Unit Test Strategy

- Add focused resolver tests for explicit token, `GITHUB_TOKEN`, `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, `GITHUB_TOKEN_SECRET_REF`, `WORKFLOW_GITHUB_TOKEN_SECRET_REF`, and `MOONMIND_GITHUB_TOKEN_REF`.
- Add `PublishService` tests proving branch push and PR creation receive explicit credential materialization without ambient `git`/`gh` state and redact token-like output.
- Add `GitHubService` tests for sanitized permission diagnostics, `X-Accepted-GitHub-Permissions`, readiness checks 403, reaction fallback 403, and targeted token probe responses.
- Add indexer tests proving `GitHubIndexer` can use the canonical resolver when no constructor token is passed.
- Run focused pytest targets during iteration, then `./tools/test_unit.sh`.

## Integration Test Strategy

- Add hermetic integration or workflow-boundary tests for the real publish service invocation shape used by task handlers/runtime activities.
- Add a boundary test proving readiness evaluation degrades optional evidence without failing unrelated evidence.
- Add a service/API boundary test for token probe output using mocked GitHub HTTP responses and no live credentials.
- Run `./tools/test_integration.sh` for required `integration_ci` coverage when integration tests are added or affected.

## Complexity Tracking

No constitution violations are planned.
