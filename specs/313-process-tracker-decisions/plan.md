# Implementation Plan: Process Verified Tracker Decisions

**Branch**: `313-process-tracker-decisions` | **Date**: 2026-05-07 | **Spec**: specs/313-process-tracker-decisions/spec.md
**Input**: Single-story feature specification from `specs/313-process-tracker-decisions/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed branch name `change-jira-issue-mm-599-to-status-in-pr-32547f89` because it is not in `001-feature-name` form. Planning continued manually from `.specify/feature.json` and the active feature directory.

## Summary

Implement MM-599 by connecting external tracker reviewer decisions to MoonMind proposal state and controlled run promotion. Current code already stores proposal delivery records, renders GitHub/Jira review issues with stored-snapshot notices, parses bounded `/moonmind` provider commands, records provider decision metadata idempotently, and supports manual API promotion from the stored proposal snapshot. The missing behavior is the trusted provider-decision ingestion boundary: authenticated GitHub/Jira webhook or recovery endpoints, actor permission checks, request-revision semantics, accepted provider approval promotion through the canonical run creation path, non-executing provider decisions with full audit state, external issue status updates, and hermetic tests proving edited tracker content never replaces stored proposal payloads. The plan uses TDD with focused unit tests for decision parsing, service state transitions, redaction, validation, and provider adapters, plus integration/boundary tests for authenticated provider decision ingestion through API/service and run creation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `TaskProposalStatus.OPEN`, `promote_proposal()`, and `dismiss_proposal()` keep normal creation separate from execution; provider approval currently marks `ACCEPTED` only | add trusted provider decision ingestion that starts execution only after verified, authorized promotion | unit + integration |
| FR-002 | partial | `parse_provider_decision()` supports `promote`, `dismiss`, `defer`, and `priority`; `request revision` is absent | add canonical `request_revision` decision support and normalize provider-native events to all five outcomes | unit |
| FR-003 | missing | no `api_service` route found for GitHub/Jira proposal webhooks or signature/shared-secret verification | add trusted webhook/recovery ingestion boundary with provider authenticity validation | unit + integration |
| FR-004 | partial | `record_provider_decision_event()` validates provider/external key and allowed actions but only uses actor text | add actor permission checks tied to configured provider identity/destination policy | unit + integration |
| FR-005 | partial | delivery/service errors use `SecretRedactor`; provider decision rejection exists for identity/action mismatches | ensure unverified/unauthorized/policy-denied decisions are rejected with sanitized persisted/output records | unit |
| FR-006 | partial | manual `/api/proposals/{id}/promote` creates a `MoonMind.Run`; provider approval only sets status `ACCEPTED` | bridge accepted provider approval to one canonical run creation using stored snapshot and idempotency | unit + integration |
| FR-007 | partial | `dismiss` and `defer` are parsed/recorded; `priority` updates priority; `request_revision` absent | implement all non-executing decisions including request revision, and keep them from creating runs | unit + integration |
| FR-008 | implemented_verified | renderers include stored-snapshot notices; `parse_provider_decision()` ignores arbitrary body text; service test proves unsafe body text is not persisted | preserve and extend coverage through provider ingestion boundary | final verify + integration |
| FR-009 | implemented_unverified | `promote_proposal()` loads stored `task_create_request` and applies bounded priority/maxAttempts/runtime controls; provider bridge not implemented | reuse stored snapshot promotion path for provider approvals and add tests for bounded controls only | unit + integration |
| FR-010 | implemented_unverified | `promote_proposal()` preserves stored payload and explicit skills by validating `CanonicalTaskPayload`; no provider approval bridge proof | add provider-promotion tests showing explicit skill selectors survive | unit |
| FR-011 | implemented_unverified | `_build_task_preview()` and `promote_proposal()` preserve authored presets and step source metadata from stored payload | add provider-promotion tests showing authoredPresets and steps[].source survive | unit |
| FR-012 | partial | `providerDecisions` metadata records actor, provider event ID, note, timestamp, result, priority, and deferUntil; external issue state and request revision missing | complete non-executing audit shape and external issue state capture | unit |
| FR-013 | partial | runtime override validation exists in manual promotion; provider decision controls do not yet promote or validate runtime overrides | validate provider-supplied runtime controls before run creation | unit |
| FR-014 | partial | `record_provider_decision_event()` deduplicates by provider event ID; no run-promotion idempotency proof for provider approvals | add end-to-end duplicate provider approval coverage proving one run | unit + integration |
| FR-015 | partial | manual promotion response returns promoted execution ID; provider decisions store metadata only | record promoted run ID in proposal delivery metadata and expose it for external updates/inspection | unit + integration |
| FR-016 | missing | no `proposal-deliveries` inspection/redeliver/sync/promote admin APIs found | add recovery surface or document reuse of equivalent proposal routes with required state | unit + integration |
| FR-017 | partial | `SecretRedactor`, delivery redaction, and safe metadata helpers exist | extend redaction tests to webhook/provider decision failures and external update payloads | unit |
| FR-018 | implemented_unverified | `spec.md` preserves MM-599 and original brief; this plan preserves MM-599 | preserve traceability through design artifacts, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | partial | manual API promotion flow exists | add provider approval scenario from verified event to one new run and external issue update | integration |
| SCN-002 | implemented_unverified | parser/service tests show edited issue body text does not replace stored snapshot | add boundary test through provider decision ingestion | unit + integration |
| SCN-003 | partial | dismiss/defer/priority provider decisions update local state; request revision and external issue state missing | complete non-executing decision scenarios | unit + integration |
| SCN-004 | partial | duplicate provider event returns previous result; no provider approval run idempotency proof | add duplicate approval and duplicate non-executing decision tests | unit + integration |
| SCN-005 | partial | identity mismatch/action policy rejections exist; actor authorization and provider verification missing | add rejected unverified/unauthorized scenarios | unit + integration |
| SCN-006 | partial | manual runtime override validation exists | add provider approval invalid-runtime control scenario | unit |
| SCN-007 | missing | no admin recovery proposal-delivery routes found | add recovery inspection/sync/promote scenario or equivalent explicit API contract | integration |
| SC-001 | partial | decision records include most fields; missing verified actor semantics/external state for all outcomes | prove accepted decisions record required fields | unit |
| SC-002 | partial | duplicate provider event handling exists; no run creation path on provider approval | prove duplicate approval creates zero additional runs | integration |
| SC-003 | implemented_unverified | existing unit tests prove parser ignores unsafe text; promotion uses stored snapshot | add provider-ingestion boundary test | unit + integration |
| SC-004 | partial | validation rejects several bad inputs; provider authenticity/actor checks missing | prove all blocked decisions stop before run creation | unit + integration |
| SC-005 | partial | dismiss/defer/priority do not create runs today; request revision absent | prove all non-executing decisions create zero runs | unit + integration |
| SC-006 | partial | manual promotion response exposes run ID; provider decisions do not update external metadata | expose promoted/non-executing outcome through proposal state and external metadata | unit + integration |
| SC-007 | implemented_unverified | `spec.md` and design artifacts preserve MM-599 | preserve through tasks and final verification | final verify |
| DESIGN-REQ-002 | partial | creation and manual promotion are separate; provider promotion incomplete | same as FR-001/FR-006/FR-014 | unit + integration |
| DESIGN-REQ-021 | partial | parser supports four of five decisions | same as FR-002/FR-007 | unit |
| DESIGN-REQ-022 | missing | no proposal webhook signature/shared-secret route found | same as FR-003 | unit + integration |
| DESIGN-REQ-023 | implemented_unverified | stored snapshot promotion and provenance preservation exist for manual promotion | verify through provider promotion path | unit + integration |
| DESIGN-REQ-024 | partial | several non-executing decisions recorded; request revision/external state incomplete | same as FR-007/FR-012 | unit + integration |
| DESIGN-REQ-025 | partial | manual runtime override validation exists; provider controls incomplete | same as FR-013 | unit |
| DESIGN-REQ-026 | missing | admin recovery APIs named in source are absent | same as FR-016/FR-015 | integration |
| DESIGN-REQ-031 | partial | delivery adapter policy/redaction exists; webhook decision policy/auth incomplete | same as FR-003/FR-004/FR-005/FR-017 | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, Temporal Python SDK, existing `TemporalExecutionService`, trusted Jira/GitHub integration services, pytest  
**Storage**: Existing `task_proposals` table and `provider_metadata`/delivery fields; no new persistent table planned unless provider decision audit or idempotency cannot safely reuse existing metadata  
**Unit Testing**: pytest through `./tools/test_unit.sh`; focused iteration with `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_proposal_activities.py -q`  
**Integration Testing**: pytest API/service boundary coverage, preferably in `tests/integration/temporal/test_proposal_review_delivery.py`; run `./tools/test_integration.sh` if any new tests are marked `integration_ci`  
**Target Platform**: MoonMind backend control plane and Temporal-backed managed execution service on Linux  
**Project Type**: Python backend workflow/control-plane service with trusted external provider integrations  
**Performance Goals**: Decision handling remains bounded per provider event; duplicate provider event checks are constant or small-list metadata scans per proposal; webhook handling performs no unbounded artifact reads  
**Constraints**: Provider credentials stay inside trusted integration boundaries; provider issue text is untrusted; run creation is idempotent; no raw secrets in artifacts/logs/comments; workflow/activity boundaries need regression coverage; internal pre-release contracts should be updated cleanly without compatibility aliases  
**Scale/Scope**: One proposal decision-ingestion story covering GitHub/Jira reviewer events, all five canonical decisions, stored-snapshot promotion, non-executing decision audit, duplicate event safety, recovery surfaces, and MM-599 traceability

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The plan bridges external review decisions into existing MoonMind orchestration instead of rebuilding agent behavior.
- II. One-Click Agent Deployment: PASS. Provider integrations remain optional and test coverage is hermetic by default.
- III. Avoid Vendor Lock-In: PASS. GitHub/Jira decisions are normalized behind provider boundaries and common proposal decision contracts.
- IV. Own Your Data: PASS. Executable snapshots and decision audit remain in MoonMind-controlled storage.
- V. Skills Are First-Class and Easy to Add: PASS. Promotion preserves explicit skill selectors and preset provenance from stored proposals.
- VI. Design for Deletion / Thick Contracts: PASS. Decision events, results, and recovery contracts isolate provider-specific logic from core proposal semantics.
- VII. Runtime Configurability: PASS. Runtime overrides are bounded request controls and validated against existing task runtime contracts.
- VIII. Modular and Extensible Architecture: PASS. Planned work is scoped to proposal service, provider decision boundary, API routes, and tests.
- IX. Resilient by Default: PASS. Provider event idempotency, run idempotency keys, retry-safe external updates, and redacted failure summaries are explicit.
- X. Facilitate Continuous Improvement: PASS. Approved proposals become auditable runs while non-executing decisions remain reviewable.
- XI. Spec-Driven Development: PASS. `spec.md`, this `plan.md`, and design artifacts preserve MM-599 before implementation.
- XII. Canonical Documentation Separation: PASS. Implementation planning stays under `specs/313-*`; canonical docs remain source requirements only.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases are planned; superseded internal decision/action names should be removed in the implementation change.

## Project Structure

### Documentation (this feature)

```text
specs/313-process-tracker-decisions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── proposal-decision-ingestion-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # created by moonspec-tasks, not this step
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   └── task_proposals.py             # existing proposal API; may gain recovery/admin surfaces or delegate to a new router
└── db/models.py                      # existing user/auth and DB base surfaces

moonmind/
├── schemas/
│   └── task_proposal_models.py       # request/response schemas for proposal decisions and recovery controls
├── workflows/
│   ├── task_proposals/
│   │   ├── delivery.py               # provider decision event/result parsing and provider adapters
│   │   ├── models.py                 # proposal status/data model
│   │   ├── repositories.py           # proposal lookup/update persistence
│   │   └── service.py                # provider decision ingestion and promotion decision orchestration
│   └── temporal/
│       └── activity_runtime.py       # proposal activity boundary when workflow-side submission/decision integration is needed
└── integrations/
    └── jira/                         # trusted Jira service primitives used by delivery/status updates

tests/
├── unit/
│   ├── api/routers/test_task_proposals.py
│   └── workflows/
│       ├── task_proposals/test_delivery.py
│       ├── task_proposals/test_service.py
│       └── temporal/test_proposal_activities.py
└── integration/
    └── temporal/test_proposal_review_delivery.py
```

**Structure Decision**: Reuse the existing task proposal service, delivery helper, SQLAlchemy model, API router, and Temporal execution service. Add a separate contract and only introduce new modules/routes if they reduce coupling around provider webhook or recovery boundaries.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |
