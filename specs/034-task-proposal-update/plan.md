# Implementation Plan: Task Proposal Targeting Policy

**Branch**: `034-task-proposal-update` | **Date**: 2026-02-20 | **Spec**: `/specs/034-task-proposal-update/spec.md`  
**Input**: Feature specification from `/specs/034-task-proposal-update/spec.md`

## Summary

Implement policy-driven targeting so worker-generated proposals can be routed to project repositories, MoonMind CI, or both while preserving canonical `taskCreateRequest` semantics. The change introduces global env knobs, per-task overrides, deterministic default slots/severity, normalized CI metadata/tags, signal-tag-aware dedup, server-side priority derivation + override provenance, and dashboard filters/badges so reviewers can triage MoonMind improvements without disrupting existing project workflows.

## Technical Context

**Language/Version**: Python 3.11 services (FastAPI API, Celery/Codex worker) plus plain ES modules/Tailwind for dashboard assets  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy, httpx, Celery 5.4, structlog, asyncpg, tailwindcss  
**Storage**: PostgreSQL for proposals/tasks, RabbitMQ 3.x for Celery broker, local object storage for artifacts  
**Testing**: `./tools/test_unit.sh` (pytest suites), targeted JS build via `npm run dashboard:css`, manual dashboard smoke tests per Acceptance Criteria  
**Target Platform**: Dockerized Linux services (`docker compose up api rabbitmq celery-worker`) with shared `/workspace` bind mounts  
**Project Type**: Multi-service backend (shared `moonmind` Python package + FastAPI API + Codex worker + static dashboard)  
**Performance Goals**: Preserve existing dedup latency and keep CI proposals visible in <2 minutes (SC-002) without adding extra DB hops per submission  
**Constraints**: Continue storing canonical `taskCreateRequest`, maintain repository + normalized-title dedup key, keep human review mandatory, enforce env-config-only knobs, avoid shared agent context refresh (not requested)  
**Scale/Scope**: Designed for thousands of proposals/day; dual-target runs must stay under per-target `maxItems` limits even when multiple detectors emit concurrently

## Constitution Check

`.specify/memory/constitution.md` is still a placeholder with no ratified principles, so no enforceable gates are defined. Documenting the absence of governance inputs and proceeding with implicit defaults (test-first, integration coverage) already captured in the Testing Strategy.  
**Gate Status**: PASS (no active constitution clauses to evaluate; action to update constitution tracked outside this feature).

## Project Structure

```text
moonmind/
├── config/settings.py                # adds policy defaults + env plumbing
├── schemas/
│   ├── agent_queue_models.py         # CreateJobRequest + canonical payload schema
│   └── task_proposal_models.py       # API DTOs / response models
├── workflows/
│   ├── agent_queue/task_contract.py  # proposalPolicy validation + normalization
│   └── task_proposals/{models.py,repositories.py,service.py}
├── agents/codex_worker/worker.py     # reads policy, emits proposals
api_service/
├── api/routers/task_proposals.py     # POST /api/proposals validation flow
├── config.template.toml              # documents env knobs
└── static/task_dashboard/dashboard.js# repository/category/tag filters
docs/
├── TaskProposalQueue.md
└── TaskQueueSystem.md
tests/
├── unit/agents/codex_worker/test_worker.py
├── unit/workflows/task_proposals/test_service.py
├── unit/api/routers/test_task_proposals.py
└── unit/config/test_settings.py
```

**Structure Decision**: Continue using the shared `moonmind` package for business logic while `api_service` exposes HTTP routes and static assets. Worker + service changes can ship independently but must share schema/config definitions.

## Complexity Tracking

No constitution exceptions required; existing architecture handles the added policy layer without introducing new project types or repositories.

## Phase 0 — Research Outcomes

1. **Centralized configuration**: `moonmind/config/settings.py` gets `TaskProposalSettings` fields (`proposal_targets_default`, `moonmind_ci_repository`) so both API and workers pull identical defaults (covers DOC-REQ-003/004).  
2. **ProposalPolicy location**: Extend `CanonicalTaskPayload` and `CreateJobRequest` with `task.proposalPolicy` to keep per-run overrides auditable (DOC-REQ-005).  
3. **Server-side priority mapping**: `TaskProposalService` derives `reviewPriority` using signal severity + tags so MoonMind submissions cannot downgrade critical proposals (DOC-REQ-007).  
4. **Metadata responsibilities**: Worker enriches `origin_metadata` (trigger repo/job/step, detector payload) while API validates before persistence (DOC-REQ-008).  
5. **Dashboard filters**: Reuse existing JS filtering pipeline in `dashboard.js` to add repository/category/tag chips without new backend endpoints (DOC-REQ-011).  
All unknowns from Technical Context were resolved through docs/TaskProposalQueue.md, so no `NEEDS CLARIFICATION` items remain heading into Phase 1.

## Phase 1 — Design & Contract Alignment

- **Data model**: Introduce `ProposalPolicy` + `EffectiveProposalPolicy` structs (see `data-model.md`) with strict enums for targets, severity floors, and per-target `maxItems`.  
- **Schema updates**: `moonmind/workflows/agent_queue/task_contract.py` accepts optional `proposalPolicy`; `moonmind/schemas/agent_queue_models.py` mirrors it for API payloads; `moonmind/schemas/task_proposal_models.py` allows derived `reviewPriority`.  
- **MoonMind metadata contract**: Worker + API enforce `[run_quality]` category, `[run_quality] …` titles, approved tag vocabulary, and presence of `origin_metadata.triggerRepo`, `origin_metadata.triggerJobId`, and `origin_metadata.signal`.  
- **Requirements traceability**: `contracts/requirements-traceability.md` maps DOC-REQ-001→012 to concrete modules/tests, ensuring every acceptance criterion has at least one implementation surface + validation hook.  
- **Quickstart alignment**: `quickstart.md` and docs/TaskProposalQueue.md outline env var usage, worker restart steps, policy override examples, and validation commands so operators can stage the rollout.

## Phase 2 — Implementation Strategy

1. **Config + schema foundation**  
   - Update `api_service/config.template.toml` and docs/TaskQueueSystem.md to surface `MOONMIND_PROPOSAL_TARGETS`/`MOONMIND_CI_REPOSITORY`.  
   - Extend `settings.TaskProposalSettings`, `SpecWorkflowSettings`, and `CodexWorkerConfig.from_env` so env overrides flow end-to-end, including default slot counts (`project=3`, `moonmind=2`) and severity vocabulary (`low|medium|high|critical`) with a default MoonMind floor of `high`.  
   - Teach `task_contract.py` + `agent_queue_models.py` how to parse/validate `proposalPolicy` (targets subset, per-target caps, severity enums) while persisting it into canonical payloads and `task_context.json`, plus emit structured logs whenever defaults are applied.  
   - Revise JSON schemas/tests so API + worker share a single source of truth; failing validation blocks Phase 3 until resolved.

2. **Worker policy evaluation (US1 / MVP)**  
   - Parse `proposalPolicy` from canonical payloads, merge with config defaults, and compute an `EffectiveProposalPolicy` (allow flags, slot counters, severity threshold) that records whether defaults or overrides were used.  
   - When reading `task_proposals.json`, fan out per target: project proposals keep original repository; MoonMind proposals rewrite repository to `settings.task_proposals.moonmind_ci_repository`, enforce `[run_quality]` titles/tags, and gate on severity/slots.  
   - Normalize dedup inputs by appending the sorted signal-tag slug (e.g., `duplicate_output+loop_detected`) to the `[run_quality]` title before computing the normalized title so repository + normalized title + tag slug stays unique per signal set.  
   - Inject origin metadata (trigger repo/job/step, detector payload, branch info) before submission; skipped MoonMind proposals log structured reasons.  
   - Preserve dedup + human-review semantics by ensuring `taskCreateRequest` stays canonical and `repository + normalized title` remain unchanged except for intentional MoonMind rewrites (tag slug suffix is part of the normalized title definition for MoonMind targets).

3. **API/service enforcement (US2)**  
   - `TaskProposalService.create_proposal` validates MoonMind payloads, forces allowed tag set, derives `reviewPriority`, persists a `priority_override_reason`, and records overrides for audit/logging.  
   - `api_service/api/routers/task_proposals.py` accepts optional `reviewPriority`, rejects MoonMind submissions missing metadata/tags, surfaces clear validation errors, and includes the override reason in API responses so dashboards/widgets can render it.  
   - Requirements include server-side severity mapping to override caller-supplied priorities whenever signals demand escalation.

4. **Dashboard + docs alignment (US2/US3)**  
   - Enhance `dashboard.js` with repository/category/tag filters plus origin metadata panes, derived priority badges, and override-reason tooltips so reviewers understand auto-escalations.  
   - Refresh docs/TaskProposalQueue.md (quickstart + release notes) and README references to explain policy usage, severity gating, and validation commands.

5. **Testing & rollout**  
   - Expand unit suites: `tests/unit/agents/codex_worker/test_worker.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, `tests/unit/config/test_settings.py`.  
   - Use `./tools/test_unit.sh` as the CI-aligned runner; include fixtures for project-only, MoonMind-only, dual-target, severity-below-threshold, missing metadata, and dedup regression cases.  
   - Manual dashboard verification after rebuilding CSS via `npm run dashboard:css` to ensure filters/tag badges load.  
   - Final smoke run: submit synthetic MoonMind proposal via API and verify reviewer dashboard + priority override behavior.

## Testing Strategy

- Primary validation via `./tools/test_unit.sh`, which orchestrates pytest across API, worker, config, and workflow modules.  
- New/updated pytest modules:
  - `tests/unit/config/test_settings.py`: env parsing + worker config propagation, including fallback behavior when `MOONMIND_PROPOSAL_TARGETS` or `maxItems` are omitted.
  - `tests/unit/agents/codex_worker/test_worker.py`: policy merging (including env-only/default scenarios), severity gating, `maxItems`, metadata injection, dedup invariants.
  - `tests/unit/workflows/task_proposals/test_service.py`: category/title/tag enforcement, priority derivation, origin metadata requirements.
  - `tests/unit/api/routers/test_task_proposals.py`: request validation + response codes when metadata/tags missing.  
- Front-end verification: rebuild dashboard assets with `npm run dashboard:css` (or `dashboard:css:min` for release) and manually test repository/category/tag filters, derived priority badges, and override-reason tooltips alongside metadata panes.  
- Acceptance smoke: run a dual-target task through the queue, inspect stored proposals via API + dashboard, and confirm severity overrides + traceability requirements are satisfied.

## Risks & Mitigations

- **Policy drift between worker and API**: Mitigate by centralizing settings + schema definitions and covering them with shared unit tests before enabling MoonMind targets.  
- **Noise from incorrect severity gating**: Provide structured logging for skipped MoonMind proposals and fixtures that cover threshold edges; ensure detectors populate severity in `task_proposals.json`.  
- **Dashboard performance regression**: Scope filters to client-side datasets already loaded, memoize tag/repository option derivation, and test with large sample payloads before deployment.  
- **Config rollout gaps**: Document env vars in `config.template.toml`, quickstart, and release notes so operators set defaults before enabling worker logic; block worker policy enforcement on config availability checks.  
- **Backward compatibility**: Keep `proposalPolicy` optional with sane defaults; add regression tests that submit legacy payloads to confirm behavior is unchanged when overrides are absent.
