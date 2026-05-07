# Implementation Plan: Proposal Review Delivery

**Branch**: `312-proposal-review-delivery` | **Date**: 2026-05-07 | **Spec**: specs/312-proposal-review-delivery/spec.md
**Input**: Single-story feature specification from `specs/312-proposal-review-delivery/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed branch name `change-jira-issue-mm-598-to-status-in-pr-dd08a68f` because it is not in `001-feature-name` form. Planning continued with `.specify/feature.json` and the active feature directory.

## Summary

Deliver MM-598 by extending the existing task proposal delivery-record foundation into provider-facing GitHub Issue and Jira issue delivery. Current code already normalizes proposal policy, persists delivery-record fields, records provider metadata, performs local open-duplicate reuse, and exposes proposal fields through `/api/proposals`. The missing behavior is the external tracker delivery layer: provider-specific issue rendering, GitHub/Jira create-or-update adapters, stored-snapshot notices, safe reviewer controls, provider-event decision normalization, allowlist enforcement at the proposal delivery boundary, and existing task run/finish-summary visibility. The plan uses TDD with focused unit tests for rendering, policy, dedup, redaction, and provider adapters, plus hermetic boundary/integration coverage for proposal submit-to-delivery behavior and persisted delivery records.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `TaskProposalRepository.find_open_duplicate()` and `TaskProposalService.create_proposal()` reuse local open duplicates by provider/repository/dedup hash, but no provider issue create/update adapter exists | add provider delivery orchestration that creates or updates exactly one GitHub/Jira issue per destination and dedup target | unit + boundary/integration |
| FR-002 | implemented_verified | `moonmind/workflows/task_proposals/models.py`, `repositories.py`, `api_service/migrations/versions/311_proposal_delivery_records.py`, and tests serialize provider/external/dedup/snapshot fields | preserve existing delivery-record fields and use them from the new delivery path | final verify |
| FR-003 | implemented_verified | `TaskProposalService._compute_dedup_fields()` and `tests/unit/workflows/task_proposals/test_service.py` cover repository-aware normalized-title dedup | preserve current dedup identity and reuse it before provider delivery | final verify |
| FR-004 | partial | local duplicate merge tests cover open record reuse, but provider metadata search/update and provider-side issue annotation are absent | add provider-side duplicate lookup/update/link behavior using local record and provider metadata before create | unit + boundary/integration |
| FR-005 | missing | no proposal-specific GitHub Issue create/update renderer or adapter found; `GitHubService` is PR/readiness-oriented | add GitHub proposal issue renderer and trusted create/update path with labels, hidden marker, links, and reviewer instructions | unit + boundary |
| FR-006 | partial | `JiraToolService.create_issue()`, `edit_issue()`, `search_issues()`, ADF helpers, and policy enforcement exist, but no proposal-specific Jira delivery adapter uses them | add Jira proposal issue renderer and trusted create/update path with ADF description, configured fields, states/triggers, links, and related issue metadata | unit + boundary |
| FR-007 | missing | no external issue rendering layer currently emits stored-snapshot notices | include a stored-snapshot notice in both GitHub Markdown and Jira ADF renderers | unit |
| FR-008 | partial | promotion uses stored `task_create_request` and API tests reject taskCreateRequest override; provider decision ingestion is not implemented | keep promotion snapshot-only and add provider command/event parsing that accepts only bounded controls | unit + boundary |
| FR-009 | missing | no proposal-specific promote/dismiss/defer/priority provider command or workflow-state handler found | define and implement reviewer action controls for GitHub commands and Jira workflow/field/comment triggers | unit + boundary |
| FR-010 | partial | `promote_proposal()` and `dismiss_proposal()` record user decisions; provider event identity and deferred/priority decisions are not modeled end to end | add provider decision event normalization and audit metadata for promote, dismiss, defer, and priority changes | unit + boundary/integration |
| FR-011 | partial | Jira tool service enforces Jira project/action policy; GitHub permission profiles exist; proposal delivery itself has no destination/action allowlist gate | enforce repository/org/Jira site/project/action policy before provider issue delivery and decision handling | unit + boundary |
| FR-012 | partial | proposal service scrubs stored metadata and trusted Jira/GitHub services keep credentials behind integration boundaries; no proposal provider adapter exists yet | ensure delivery adapters resolve credentials internally, redact errors, and never place credentials in issue text, logs, or API responses | unit + boundary |
| FR-013 | partial | Jira/GitHub services expose sanitized summaries; proposal delivery path does not yet normalize provider errors | add sanitized proposal delivery error shape and persist/report recoverable next action | unit + boundary |
| FR-014 | partial | delivery records can carry `task_snapshot_ref` and external URLs, but no issue renderer links run evidence/artifact refs | render links to stored snapshots, run evidence, logs, and diagnostics by reference only | unit |
| FR-015 | partial | `/api/proposals` serializes external fields; no evidence found that task run details or finish summaries expose delivered proposal links | surface delivery status and external issue links in existing run detail/finish-summary outputs | unit + integration |
| FR-016 | implemented_unverified | `spec.md` preserves MM-598 and original brief; this plan preserves it | preserve MM-598 through plan, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | missing | no GitHub proposal issue create/update flow | add GitHub delivery scenario coverage | unit + boundary |
| SCN-002 | partial | trusted Jira create/edit tools exist, but no proposal delivery flow | add Jira delivery scenario coverage | unit + boundary |
| SCN-003 | partial | local duplicate record reuse exists; provider-side duplicate create/update is missing | add duplicate provider issue update/link coverage | unit + boundary |
| SCN-004 | missing | no reviewer action rendering/handling from provider surfaces | add reviewer action instruction and decision handling tests | unit + boundary |
| SCN-005 | partial | stored-snapshot promotion exists, but provider text/command ingestion is absent | add safety tests proving edited provider text cannot replace the stored snapshot | unit + boundary |
| SCN-006 | partial | Jira/GitHub service-level policies exist; proposal delivery policy gate is absent | add blocked destination/action tests with sanitized output | unit + boundary |
| SC-001 | partial | local dedup tests cover one record path, not provider surfaces | prove repeated proposals produce one open external issue in covered delivery tests | unit + boundary |
| SC-002 | missing | no renderer tests for GitHub/Jira review context completeness | add renderer contract tests for required elements | unit |
| SC-003 | implemented_unverified | promotion override rejection exists; provider edited-text safety lacks coverage | add provider decision safety tests; keep existing promotion tests | unit + boundary |
| SC-004 | partial | redaction helpers and trusted services exist; no delivery-specific tests | add allowlist/redaction tests at proposal delivery boundary | unit + boundary |
| SC-005 | partial | API proposal serialization exists; run detail/finish summary linkage not proven | add run detail or finish summary exposure tests | unit + integration |
| SC-006 | implemented_unverified | `spec.md` and this plan preserve MM-598 and mapped source design IDs | preserve through tasks and final verification | final verify |
| DESIGN-REQ-001 | partial | delivery records exist; external issue create/update does not | same as FR-001/FR-002/FR-015 | unit + boundary |
| DESIGN-REQ-014 | partial | local dedup exists; provider duplicate lookup/update missing | same as FR-003/FR-004 | unit + boundary |
| DESIGN-REQ-015 | missing | no GitHub proposal issue renderer/adapter | same as FR-005/FR-007/FR-009/FR-014 | unit + boundary |
| DESIGN-REQ-016 | partial | trusted Jira create/edit/search and ADF exist; no proposal-specific adapter | same as FR-006/FR-007/FR-009/FR-014 | unit + boundary |
| DESIGN-REQ-027 | partial | stored snapshot promotion exists; external rendered issue safety missing | same as FR-007/FR-008/FR-014 | unit + boundary |
| DESIGN-REQ-031 | partial | trusted Jira/GitHub primitives exist; proposal provider adapter boundary missing | same as FR-010/FR-011/FR-012/FR-013 | unit + boundary |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, SQLAlchemy async ORM, FastAPI, Temporal Python SDK activity boundaries, `httpx`, existing Jira trusted tool service, existing GitHub service/auth helpers, pytest  
**Storage**: Existing `task_proposals` table and `311_proposal_delivery_records` migration fields; no new persistent table planned unless provider decision audit cannot reuse existing proposal metadata/decision fields  
**Unit Testing**: pytest through `./tools/test_unit.sh`; focused iteration with `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/api/routers/test_task_proposals.py -q`
**Integration Testing**: pytest boundary/integration coverage for proposal submit-to-delivery behavior and persisted delivery records; focused iteration with `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q`; run `./tools/test_integration.sh` if `integration_ci` tests are added
**Target Platform**: MoonMind backend control plane and Temporal worker runtime on Linux  
**Project Type**: Python backend workflow/runtime service with trusted external provider integrations  
**Performance Goals**: Dedup lookup and rendering remain bounded per proposal candidate; provider delivery performs local validation and duplicate checks before external calls  
**Constraints**: Provider credentials stay inside trusted integration boundaries; delivery is idempotent and retry-safe; external issue text is never executable; workflow/activity payload boundaries require regression tests; canonical docs stay desired-state only  
**Scale/Scope**: One proposal delivery story covering GitHub Issues and Jira issues, dedup-first delivery, rendered review artifacts, safe decision controls, provider policy enforcement, and run-visible delivery links

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan adds provider adapter boundaries around external trackers and does not alter agent cognition.
- II. One-Click Agent Deployment: PASS. Uses existing optional Jira/GitHub integrations and keeps local development available without mandatory external credentials.
- III. Avoid Vendor Lock-In: PASS. GitHub and Jira behavior lives behind proposal provider adapters with a portable delivery-record contract.
- IV. Own Your Data: PASS. Stored proposal snapshots and delivery records remain in operator-controlled storage.
- V. Skills Are First-Class and Easy to Add: PASS. Proposal delivery preserves task payload and skill intent without re-expanding live presets.
- VI. Design for Deletion / Thick Contracts: PASS. Renderer, adapter, and decision contracts make provider-specific code replaceable.
- VII. Runtime Configurability: PASS. Delivery provider and metadata continue through task proposal policy and settings defaults.
- VIII. Modular and Extensible Architecture: PASS. Changes are scoped to proposal delivery service/adapter boundaries and existing API/workflow surfaces.
- IX. Resilient by Default: PASS. Dedup-first delivery, provider event idempotency, redacted failure summaries, and retry-safe updates are explicit requirements.
- X. Facilitate Continuous Improvement: PASS. The feature makes generated follow-up work reviewable in external trackers.
- XI. Spec-Driven Development: PASS. `spec.md`, `plan.md`, and design artifacts preserve MM-598 before implementation.
- XII. Canonical Documentation Separation: PASS. Migration and execution details remain under `specs/312-*`; canonical docs remain source requirements.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases are planned; internal proposal contracts should be updated consistently with tests.

## Project Structure

### Documentation (this feature)

```text
specs/312-proposal-review-delivery/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── proposal-review-delivery-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # created by /speckit.tasks, not this step
```

### Source Code (repository root)

```text
moonmind/
├── integrations/
│   └── jira/
│       ├── adf.py
│       ├── models.py
│       └── tool.py
├── workflows/
│   ├── adapters/
│   │   └── github_service.py
│   ├── task_proposals/
│   │   ├── models.py
│   │   ├── repositories.py
│   │   ├── service.py
│   │   └── delivery.py          # planned provider adapter/rendering boundary
│   └── temporal/
│       └── activity_runtime.py

api_service/
├── api/routers/task_proposals.py
├── api/routers/task_dashboard.py
├── db/models.py
└── migrations/versions/

tests/
├── unit/
│   ├── api/routers/test_task_proposals.py
│   ├── integrations/test_jira_tool_service.py
│   └── workflows/
│       ├── task_proposals/test_service.py
│       ├── task_proposals/test_delivery.py
│       └── temporal/test_proposal_activities.py
└── integration/
    └── temporal/ or service/API boundary tests as needed
```

**Structure Decision**: Reuse existing proposal service, repository, API, Temporal activity, Jira tool, and GitHub service boundaries. Add a proposal delivery module only if implementation needs a focused provider adapter/renderer boundary; add migrations only if existing delivery-record fields cannot carry required external issue and decision metadata.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |
