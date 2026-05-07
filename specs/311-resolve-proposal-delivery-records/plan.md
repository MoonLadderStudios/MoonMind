# Implementation Plan: Resolve Proposal Policy and Delivery Records

**Branch**: `change-jira-issue-mm-597-to-status-in-pr-07dad35c` | **Date**: 2026-05-07 | **Spec**: specs/311-resolve-proposal-delivery-records/spec.md
**Input**: Single-story feature specification from `specs/311-resolve-proposal-delivery-records/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed branch name `change-jira-issue-mm-597-to-status-in-pr-07dad35c` because it is not in `001-feature-name` form. Planning continued with `.specify/feature.json` and the active feature directory.

## Summary

Implement MM-597 by making proposal submission deterministically resolve project vs MoonMind delivery policy, repository targets, deduplication, workflow-origin metadata, and durable delivery-record fields before provider-specific issue delivery. Existing proposal service, repository, task contract policy helpers, and Temporal proposal submit activity provide partial foundations, but the current behavior lacks delivery-provider routing, allowlist/provider metadata persistence, open-duplicate update/link behavior, canonical snake_case origin metadata, and several desired delivery-record fields. The implementation should extend the existing Python backend proposal service and Temporal activity boundary with unit tests plus boundary/integration-style tests that exercise real service/repository persistence and activity submission behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `proposal_submit` builds `EffectiveProposalPolicy`; `TaskProposalService.create_proposal` validates and persists proposals | centralize deterministic resolved policy before service persistence and expose decision metadata | unit + boundary |
| FR-002 | partial | `build_effective_proposal_policy()` merges defaults and policy for targets, caps, severity floor, and default runtime | preserve explicit candidate/task values over defaults and record which defaults were applied | unit |
| FR-003 | partial | policy helper enforces capacity and severity; destination allowlists and approved tag gates are incomplete | add allowlist/destination validation and full gate decisions before delivery | unit + boundary |
| FR-004 | implemented_unverified | project candidates preserve `payload.repository` through `TaskProposalService.create_proposal` | add tests proving project target repository is retained after policy resolution | unit |
| FR-005 | partial | MoonMind repository handling exists in `TaskProposalService._enforce_moonmind_policy`; `proposal_submit` only reaches MoonMind path after project slots are exhausted | route MoonMind-targeted run-quality proposals explicitly after category/severity/tag gates pass | unit + boundary |
| FR-006 | partial | `TaskProposal` persists proposal rows before notification; external provider delivery identity is not represented | extend delivery-record creation/update semantics before provider issue delivery | unit + integration_ci if DB model changes |
| FR-007 | partial | `TaskProposal` has repository, dedup, status, title, summary, category, tags, priority, task snapshot, origin, decision, and timestamps | add or document selected canonical subset; add missing provider/external issue/delivery/sync/provider metadata fields as needed | unit + integration_ci if DB model changes |
| FR-008 | implemented_unverified | `_compute_dedup_fields()` derives key/hash from repository and title | add explicit tests for canonical repository + normalized title dedup identity | unit |
| FR-009 | partial | repository can `list_similar()` by dedup hash for already-created proposals | search local open delivery records and provider metadata before creating a new record | unit + integration/boundary |
| FR-010 | missing | `create_proposal()` always calls repository `create_proposal()` for valid submissions | update, link, or comment on open duplicates instead of creating duplicate reviewer-facing records | unit + integration/boundary |
| FR-011 | partial | `TaskProposalOriginSource.WORKFLOW` exists; `proposal_submit` uses workflow source | persist `origin.id = workflow_id` semantics and align type/field handling with existing UUID column constraints or an explicit field choice | unit + integration/boundary |
| FR-012 | partial | activity currently emits `workflow_id` and `temporal_run_id`, but also camelCase `triggerRepo` and `triggerJobId`; a test asserts camelCase | normalize workflow-origin metadata to snake_case and update tests | unit + boundary |
| FR-013 | missing | no explicit provider-specific metadata object was found on `TaskProposal` | add or explicitly document a provider-specific metadata container separate from canonical fields | unit + integration_ci if DB model changes |
| FR-014 | implemented_unverified | `spec.md` preserves MM-597 and canonical preset brief | preserve issue key through plan, tasks, verification, commit, and PR metadata | final verify |
| SC-001 | partial | existing tests cover caps/defaults and some policy behavior | add explicit-over-default, allowlist rejection, capacity/gate rejection, and successful defaulted delivery tests | unit |
| SC-002 | partial | existing tests cover MoonMind metadata/priority but not explicit repository rewrite decision | add project-preserve and MoonMind-rewrite tests | unit + boundary |
| SC-003 | partial | dedup key/hash and `list_similar()` exist | add local-record, provider-metadata, and no-duplicate paths | unit + integration/boundary |
| SC-004 | missing | no existing duplicate update/link/comment path found | add tests proving open duplicates avoid new reviewer-facing records | unit + integration/boundary |
| SC-005 | partial | current record has many canonical fields but lacks provider/external delivery and provider metadata fields | add record-field and metadata-separation evidence | unit + integration_ci if DB model changes |
| SC-006 | partial | workflow origin source exists; metadata keys are mixed snake_case/camelCase | add snake_case origin tests and update activity/service behavior | unit + boundary |
| SC-007 | implemented_unverified | MM-597 is preserved in `spec.md` and this plan | preserve through tasks and verification | final verify |
| DESIGN-REQ-001 | partial | see FR-001/FR-002 | same as mapped FRs | unit + boundary |
| DESIGN-REQ-002 | partial | `TaskProposalPolicy` lacks delivery provider/GitHub/Jira fields from source section 4.2 | expand policy model or record selected out-of-scope subset explicitly | unit |
| DESIGN-REQ-003 | partial | policy resolution exists but does not persist a complete resolved delivery decision | add resolved policy/delivery decision output to records or metadata | unit + boundary |
| DESIGN-REQ-004 | partial | project/MoonMind repository handling exists but routing order and gates are incomplete | implement explicit target classification and repository rewrite | unit + boundary |
| DESIGN-REQ-005 | partial | current delivery record model has several canonical fields, but not provider/external URL/delivered/synced/provider metadata | add missing model fields or documented subset with tests | unit + integration_ci if DB model changes |
| DESIGN-REQ-006 | partial | dedup identity exists; duplicate avoidance is not applied before create | add pre-create local/provider duplicate lookup and update/link behavior | unit + integration/boundary |
| DESIGN-REQ-007 | partial | source enum includes workflow; metadata keys currently conflict with source requirement | normalize origin id/metadata and update tests | unit + boundary |
| DESIGN-REQ-008 | missing | provider-specific metadata container not found | separate provider metadata from canonical fields | unit + integration_ci if DB model changes |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 task contract models, SQLAlchemy async ORM, Temporal Python SDK activity boundary helpers, FastAPI proposal API schemas, pytest  
**Storage**: Existing `task_proposals` table and proposal repository; likely Alembic/SQLAlchemy model changes if provider/external delivery fields or provider metadata are added  
**Unit Testing**: pytest through `./tools/test_unit.sh`; focused iteration with `python -m pytest tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py -q`  
**Integration Testing**: pytest boundary/integration coverage for real proposal repository/service persistence; use `./tools/test_integration.sh` only if DB migration or compose-backed integration_ci coverage is added  
**Target Platform**: MoonMind backend control plane and Temporal worker runtime on Linux  
**Project Type**: Python backend workflow/runtime service with persisted control-plane records  
**Performance Goals**: Policy and dedup resolution remain bounded to submitted candidate count; local duplicate search uses indexed dedup fields; no provider call before local validation and duplicate checks  
**Constraints**: Trusted provider credentials stay outside agent/runtime code; external side effects must be retry-safe and deduplicated; workflow/activity payload changes must remain boundary-tested; canonical docs remain desired-state only  
**Scale/Scope**: One proposal submission/delivery-record story covering policy resolution, repository target choice, deduplication, origin metadata, and delivery-record persistence

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan strengthens MoonMind's orchestration and proposal delivery boundary without changing agent cognition.
- II. One-Click Agent Deployment: PASS. Uses existing local services and optional provider integrations; no mandatory new external dependency.
- III. Avoid Vendor Lock-In: PASS. Provider-specific metadata is explicitly separated from canonical delivery records.
- IV. Own Your Data: PASS. Proposal delivery records remain operator-controlled data.
- V. Skills Are First-Class and Easy to Add: PASS. Proposal policy does not recompute or mutate skill selection semantics.
- VI. Design for Deletion / Thick Contracts: PASS. Work is centered on deterministic contracts and persisted audit records.
- VII. Runtime Configurability: PASS. Proposal routing remains controlled by task policy and operator defaults.
- VIII. Modular and Extensible Architecture: PASS. Changes are scoped to proposal policy/service/repository/activity boundaries.
- IX. Resilient by Default: PASS. Idempotent dedup-first delivery and visible failures are explicit goals.
- X. Facilitate Continuous Improvement: PASS. Feature improves reviewable follow-up proposal reliability.
- XI. Spec-Driven Development: PASS. `spec.md`, `plan.md`, and design artifacts preserve traceability before implementation.
- XII. Canonical Documentation Separation: PASS. Planning details remain under `specs/311-*`; canonical docs are source requirements only.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases are planned; internal proposal contracts should be updated consistently with tests.

## Project Structure

### Documentation (this feature)

```text
specs/311-resolve-proposal-delivery-records/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── proposal-delivery-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # created by /speckit.tasks, not this step
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── temporal/
│   │   └── activity_runtime.py
│   ├── task_proposals/
│   │   ├── models.py
│   │   ├── repositories.py
│   │   └── service.py
│   └── tasks/
│       └── task_contract.py

api_service/
├── api/routers/task_proposals.py
├── db/models.py
└── config.template.toml

tests/
├── unit/
│   └── workflows/
│       ├── task_proposals/test_service.py
│       └── temporal/test_proposal_activities.py
└── integration/
    └── temporal/ or workflow/service tests as needed for persisted delivery records
```

**Structure Decision**: Reuse the existing proposal activity, task contract, service, repository, and API surfaces. Add migrations/integration coverage only if the chosen delivery-record subset requires persisted schema changes.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |
