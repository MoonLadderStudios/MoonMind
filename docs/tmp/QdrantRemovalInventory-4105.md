# Qdrant / Built-in Vector Retrieval Consumer Inventory — #4105

Parent: MoonLadderStudios/MoonMind#4103. This issue (#4105) owns the
**admission + authoring + plan-compilation** contract. Execution/cutover,
Compose deletion, ManifestIngest redesign, docs migration, and worker/CLI
removal belong to sibling children of #4103 (referenced below as
“sibling-execution/cutover”) and are explicitly out of scope here
(#3948 owns ManifestIngest public compile/execute semantics).

Dispositions: **delete** (remove in owning change), **retain-without-vector**
(new writes carry no vector behavior; historical reads stay), **external-only**
(already-supported explicitly-external integration with a surviving
implementation, not native vector).

## A. Shared admission boundary — owned by #4105 (this change)

| Consumer | Disposition | Notes |
|---|---|---|
| `moonmind/workflows/executions/execution_contract.py` — `reject_retired_vector_fields` / `strip_absent_vector_fields` | retain-without-vector | New: explicit `rag` / `followUpRetrieval` rejected with actionable message; absent/empty/disabled stripped, never silently weakened. Wired into `WorkflowExecutionSpec` + `CanonicalWorkflowExecutionPayload`. |
| `api_service/api/routers/executions.py` submit validation + create lift + recurring target builder | retain-without-vector | Explicit retired fields → 422 before Temporal start; absent residue stripped. Historical detail projections (`retrieval` read block) untouched. |
| `moonmind/schemas/agent_runtime_models.py` `AgentExecutionRequest` | retain-without-vector | Explicit `parameters.rag` / `followUpRetrieval` → validation error before host launch. |
| `moonmind/schemas/checkpoint_branch_models.py` create/continue/fork | retain-without-vector | Explicit `followUpRetrieval` → validation error; absent parses for history. |
| `api_service/api/routers/retrieval_gateway.py` `_bridge_authoritative_issue` | retain-without-vector | New capability issuance from an enabled launch snapshot → 410 retired (no mint, no event write). Diagnostics/result reads unchanged (historical). |

## B. Authoring surfaces — owned by #4105 (this change)

| Consumer | Disposition | Notes |
|---|---|---|
| `frontend/src/lib/contextRetrievalAuthoring.ts` | retain-without-vector | `compile…` returns `{}`; `hasAuthored…` false (no draft/hidden persistence); `parse…` kept for historical reads; new `hasRetiredRetrievalParameters` surfaces “resubmit without” guidance. |
| `frontend/src/components/ContextRetrievalControls.tsx` | delete (controls) / retain notice | All inputs/hidden state removed; retired notice only. Callers keep mounting it so no invisible form state survives. |
| `frontend/src/entrypoints/workflow-start.tsx` (create/edit/rerun) | retain-without-vector | Submit/draft paths now send no retrieval fields (`compile {}` + `hasAuthored false`). Historical parse kept for reading source payloads. `buildEditParametersPatch` strips inherited + submitted `rag` / `followUpRetrieval` unconditionally. |
| `frontend/src/entrypoints/schedules.tsx` | retain-without-vector | Read/write helpers strip `rag` / `followUpRetrieval` on save; raw JSON remains the editor. |
| `frontend/src/entrypoints/workflow-detail.tsx` (rerun/branch diagnostics) | retain-without-vector | Rerun compiles to `{}`; follow-up diagnostics query kept for historical evidence. |
| `frontend/src/lib/remediationCreateDraft.ts` | retain-without-vector | Drafts no longer persist `contextRetrieval`; type kept optional for reading old drafts. |
| `frontend/src/entrypoints/omnigent-inventory.tsx`, `WorkflowRowActionsMenu.tsx` (references) | retain-without-vector | No authoring authority; left readable. |
| `frontend/src/generated/openapi.ts` retrieval schemas | retain-without-vector | Regenerate from retired backend schemas in the owning regeneration change; no hand edits here. |

## C. Plan compilation — owned by #4105 (this change)

| Consumer | Disposition | Notes |
|---|---|---|
| `moonmind/omnigent/codex_execution_decisions.py` `compile_follow_up_retrieval_policy` / `enforce_required_follow_up_retrieval` | retain-without-vector | Compile always `{"enabled": false}` (no vector descriptors in new plans); any explicit authored request raises `OMNIGENT_RETIRED_VECTOR_RETRIEVAL` before launch. Unrelated profile/credential/qualification/approval/publication/artifact authority untouched. |
| `moonmind/omnigent/profile_bound_execution.py` effective-launch builder + coordinator | retain-without-vector | Persists `{"enabled": false}` snapshot; explicit requests fail via (C) before launch. |
| `api_service/services/omnigent_execution_plan_service.py` plan compiler | retain-without-vector | Inherits (C); no new vector policy materialized. |
| `moonmind/workflows/checkpoint_branches.py` bundle builder | retain-without-vector | Historical bundle read kept; new branch-turn admission enforced by request models (A). |
| `moonmind/workflows/temporal/workflows/run.py` agent-request forwarding | retain-without-vector | `rag` / `followUpRetrieval` no longer forwarded into request parameters (no-op post-admission; keeps pre-retirement in-flight replay deterministic — cutover child coordinates in-flight decoding/retirement). |
| `api_service/services/checkpoint_branch_turn_execution.py`, `checkpoint_branches.py`, `checkpoint_branches_service.py` | retain-without-vector | Stored diagnostics readable; new launches validate via `AgentExecutionRequest` (A). |

## D. Execution / runtime / storage — sibling of #4103 (NOT this change)

Native Qdrant/embedding execution, collection/overlay administration, index
health, CLI, workers, Compose, packaging, and ManifestIngest-adjacent names
that do not contain `qdrant` are inventoried here and explicitly deferred to
the sibling execution/cutover child. This change does not delete them, invent
a replacement provider registry, or redesign generic ManifestIngest.

| Consumer | Disposition | Owner |
|---|---|---|
| `moonmind/rag/*` (service, qdrant_client, embedding, settings, guardrails, context_injection, context_pack, planning, overlay, overlay_cleanup, long_term_memory, telemetry, cli) | delete (sibling) | sibling-execution/cutover |
| `api_service/api/routers/retrieval_gateway.py` query/index-health execution paths (`POST /context`, `GET /index-health`, session-result budget) | delete (sibling) | sibling-execution/cutover |
| `api_service/retrieval_capabilities.py` live capability registry + evidence store | delete (sibling) | sibling-execution/cutover |
| `moonmind/agents/codex_worker/worker.py`, `moonmind/workflows/temporal/activities/omnigent_session_activities.py`, `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/skills/ops_diagnostics_execution.py` (retrieval wiring) | delete/retain-without-vector per file (sibling decides) | sibling-execution/cutover |
| `moonmind/config/settings.py` RAG/Qdrant settings, `docker-compose.yaml` + `tools/get-qdrant.py`, `.env-template` retrieval env | delete (sibling) | sibling-execution/cutover |
| `moonmind/cli.py`, `moonmind/rag/cli.py`, `moonmind/manifest/*` CLI/registration paths carrying embedding/collection/overlay flags | delete (sibling) | sibling-execution/cutover |
| `api_service/services/omnigent_agent_*`, `provider_profile_creation.py`, `profile_execution_selection.py`, `presets/catalog.py`, `remediation_actions.py`, `settings_change_*`, `execution_integrations.py`, `workflow_console*.py`, `agent_runs.py`, `omnigent_bootstrap/bridge/native_ui.py` collection/overlay-adjacent handling | audit + delete vector-only branches (sibling) | sibling-execution/cutover |
| `docs/**` (`Rag/*`, `Memory/*`, `ManagedAgents/WorkerVectorEmbedding.md`, `MoonMindArchitecture.md`, `MoonMindRoadmap.md`, `UI/CreatePage.md`), `README.md`, `examples/*`, `memory/omnigent-3514-followup-retrieval-authoring.md` | docs migration (sibling) | sibling-execution/cutover |
| `tests/**` rag/retrieval/manifest suites, `frontend/src/**/*.test.*` retrieval suites | update alongside owning code change | #4105 updates admission/authoring/plan tests only; execution-suite deletion with sibling |

## E. Truthful-error / no-rescue rules (this change + sibling)

- Absent optional enrichment (no/empty/disabled vector fields) → normal admission, no vector behavior.
- Explicit unsupported retrieval → actionable field/capability validation (422 / model validation / `OMNIGENT_RETIRED_VECTOR_RETRIEVAL` / 410 on gateway issuance) **before** Temporal start, host launch, paid calls, or external writes. No silent field-drop with changed semantics.
- Authorization failures keep their existing codes; no scope-widening scan, substitute credential, or invented external backend rescues an invalid request.
- In-flight decoding and schedule retirement coordination belong to the cutover sibling; this change never mutates immutable admitted snapshots and retains no indefinite new-write alias.
