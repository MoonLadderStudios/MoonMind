# Task → Workflow Terminology Cleanup Plan

**Status:** WP1-WP4 complete; WP5-WP9 hard-switch implementation is complete for the remaining agent-run identifier family
**Audited at:** 2026-06-09, commit `638cdc278`
**Updated at:** 2026-06-10
**Canonical target semantics:** `docs/Temporal/WorkflowLanguageHardSwitchPlan.md` (referred to below as "the hard-switch plan"). This document does not redefine the target model; it inventories what is still task-named after PRs #2395 and the `tasks → workflows` route rename, and sequences the remaining work.

## Progress update: 2026-06-10

Completed:

- **WP1:** Stale `docs/Tasks/...` references are removed from live docs, agent instructions, skill instructions, and tests. The only remaining `docs/Tasks` mentions are in this historical cleanup plan and the terminology checker rule text.
- **WP2:** README and managed-agent documentation use **workflow-scoped session** and workflow-start copy.
- **WP3:** `docs/UI/WorkflowConsoleArchitecture.md` and `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md` are the active docs; the former task compatibility document is gone from live docs and tests.
- **WP4:** Docs/instruction-scope terminology enforcement exists in `tools/check_terminology.sh` and passes. The gate now also blocks canonical docs and checked-in agent instruction bundles from sending rollout/status tracking back to `specs/<feature>/` or MoonSpec feature artifacts.

Still open:

- **Broader task-shaped snapshot terminology:** The root user workflow type, workflow create envelope, workflow proposal contract, preset/recurring database names, frontend preset routes, code-scope enforcement, and agent-run identifiers are switched. Broader task-shaped snapshot keys such as original-input authoring fields and runtime draft-shape metadata remain compatibility-sensitive and should be handled as a separate cutover if the product contract is renamed further.

Completed in the hard-switch pass:

- `MoonMind.UserWorkflow` is the current user Workflow Execution type for new starts and workflow worker registration; `legacy_run` mode is rejected by the current build while explicit cutover records still name `MoonMind.Run` for drain decisions.
- API create/remediation paths now require workflow-shaped envelopes (`type: "workflow"`, `payload.workflow`) and original-input snapshots use `workflowShape`, `workflow`, and `authoredWorkflowInput`.
- Step ledger refs and execution-detail API projection now emit `agentRunId` for managed agent-run observability instead of `taskRunId`.
- Workflow proposal storage/wire contracts use `workflowCreateRequest` and `workflowSnapshotRef`; proposal DB tables/columns were renamed to `workflow_proposals` and `workflow_*`.
- Preset and recurring schedule database identifiers were renamed from task-prefixed names (`presets`, `preset_versions`, `recurring_workflow_definitions`, `recurring_workflow_runs`, and `agent_run_live_sessions`).
- Frontend workflow start/detail surfaces now use the renamed workflow type, preset route fallbacks, workflow proposal payloads, and `agentRunId` in execution detail.
- `tools/verify_workflow_terminology.py` now enforces the hard-switch surfaces above outside explicit cutover fixtures and replay-stable patch IDs.
- The managed-session, workload, remediation context/tools, agent-run observability, memory, pentest, API principal headers, frontend detail routes, and related docs/tests now use `agentRunId` / `agent_run_id` for managed agent-run identifiers.
- Runtime terminology enforcement blocks the old task-run identifier family across `api_service/`, `moonmind/`, `frontend/src/`, and `tests/`.
- A residual scan for the former task-run identifier spellings is clean outside historical rollout notes and this temporary cleanup plan.

## Reserved terms (do not remove)

Per the hard-switch plan §1 and §5, the following remain correct and are excluded from every count below: `Temporal Task`, `Workflow Task`, `Activity Task`, `Task Queue` / `task_queue`, `Jira task`, `Codex provider task`. All other unqualified MoonMind "task" usage is residual.

## Audit findings

### A. Stale `docs/Tasks/` path references — broken links (high priority, zero risk)

`docs/Tasks/` no longer exists, but **79 references across 44 files** still point at it. Every one is a broken link or a misdirected agent instruction.

| Surface | Files | Notes |
| --- | --- | --- |
| `AGENTS.md` (+ `CLAUDE.md` symlink) | 5 refs | Tells agents to read `docs/Tasks/AgentSkillSystem.md` and `docs/Tasks/SkillAndPlanContracts.md`, which do not exist. Actively misdirects every agent session. Correct targets: `docs/Steps/SkillSystem.md` and `docs/Workflows/SkillAndPlanContracts.md`. |
| `GEMINI.md` | 5 refs | Same misdirection. |
| `.agents/skills/jira-implement/SKILL.md` | 1 ref | Skill instruction bundle. |
| `docs/**` (~38 files) | ~64 refs | Cross-links left behind by the rename, e.g. `docs/Steps/SkillSystem.md` (6), `docs/Workflows/SkillAndPlanContracts.md` (5), `docs/UI/CreatePage.md` (4), `docs/Workflows/WorkflowProposalSystem.md` (3), `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` (3), `docs/MoonMindArchitecture.md` (2), plus ~32 more with 1–3 each. |
| Tests | `tests/unit/workflows/temporal/test_temporal_worker_runtime.py:1080`, `tests/unit/api/routers/test_executions.py:3496,3539` | Example instruction strings reference `docs/Tasks/WorkflowProposalSystem.md`. |

### B. Docs still carrying the task product concept (conceptual rewrites)

1. **`docs/UI/MissionControlArchitecture.md`** — still *declares* the superseded model: "The product surface remains task-oriented", "**task** is the primary product term", and documents `/tasks/*` as the primary route table. Directly contradicts the hard-switch plan and `docs/Temporal/WorkflowExecutionProductModel.md`. Hard-switch plan §16.1 calls for rewrite as `docs/UI/WorkflowConsoleArchitecture.md`. ~67 residual task lines.
2. **`docs/Temporal/TaskExecutionCompatibilityModel.md`** — the task→workflow bridge doc the hard-switch plan explicitly removes ("This plan removes that bridge rather than preserving it"). It is pinned by name in a doc test, so deletion must update that test.
3. **`docs/Temporal/RunHistoryAndRerunSemantics.md`** — §16.1 renames it to `WorkflowRunHistoryAndNewRunSemantics.md`; ~32 residual task lines.

### C. Residual task prose across docs

After excluding reserved terms, the heaviest files (residual occurrences): `docs/UI/MissionControlArchitecture.md` (67), `docs/UI/WorkflowDetailsPage.md` (44), `docs/Steps/SlashCommands.md` (43), `docs/Temporal/WorkflowSchedulingGuide.md` (41), `docs/Artifacts/ArtifactPresentationContract.md` (40), `docs/Workflows/PrMergeAutomation.md` (37), `docs/ManagedAgents/DockerOutOfDocker.md` (36), `docs/Temporal/RunHistoryAndRerunSemantics.md` (32), `docs/MoonMindArchitecture.md` (30), `docs/Workflows/ImageSystem.md` (29), `docs/Workflows/WorkflowProposalSystem.md` (27), `docs/ManagedAgents/OAuthTerminal.md` (26), `docs/UI/CreatePage.md` (25), `docs/Observability/LiveLogs.md` (23), `docs/ManagedAgents/ManagedAgentArchitecture.md` (22), `docs/MoonMindRoadmap.md` (21), `docs/Steps/SkillSystem.md` (21), plus a long tail of ~20 files with 5–19 each.

Caveat: many of these lines quote real code identifiers (`TaskPayload`, `task_proposals`, `taskId` JSON keys). Per the convention established in PR #2395, quoted identifiers must track the code rename (work packages 5–8 below), not be rewritten ahead of it — otherwise docs and code drift.

**Open terminology decision:** "task-scoped session" / "task-scoped managed-session runtime" appears throughout `README.md`, `docs/MoonMindArchitecture.md`, and ManagedAgents docs as a load-bearing product term. The hard-switch plan does not name a replacement. Proposed: **workflow-scoped session**. This should be decided once and applied everywhere in WP2.

### D. README.md and agent instruction files

- `README.md:24–25` — "your first task", "submit a task!" (product-concept residuals).
- `README.md:89,111` — "task-scoped managed-session" (see open decision above).
- `README.md:66,70,91` — generic-English "tasks"; line 70 "Scheduled and recurring tasks" is tied to the `recurring_task_*` feature naming (renames with WP7).
- `AGENTS.md`/`GEMINI.md` — beyond the stale paths in (A), the "Documentation: canonical vs feature artifacts" section still directs migration plans to `specs/<feature>/`. **Decision recorded 2026-06-09: `specs/` is no longer a checked-in source of guidance; migration/rollout plans go in `docs/tmp/`.** Update both files.

### E. Backend code (hard-switch plan §11, Phases 3–4)

~1,010 task occurrences across 97 files in `moonmind/` (mix of reserved Task Queue usage and product residuals), plus 294 across 37 files in `api_service/`. Product-concept clusters:

| Cluster | Key paths |
| --- | --- |
| Task contract package | `moonmind/workflows/tasks/` — `task_contract.py` (136), `payload.py` (`TaskPayload`), `routing.py`, `job_types.py`, `runtime_inheritance.py` (21), `preset_goal_scheduler.py`, `model_resolver.py`, `prepared_context.py` |
| Task proposals | `moonmind/workflows/task_proposals/` (service.py 45), `moonmind/schemas/task_proposal_models.py`, `api_service/api/routers/task_proposals.py` (16) |
| Task templates / presets | `api_service/services/task_templates/`, `api_service/api/routers/task_step_templates.py`, `api_service/data/task_step_templates/*.yaml`, `moonmind/agents/cli/task_templates.py` |
| Recurring tasks | `moonmind/workflows/recurring_tasks/`, `api_service/api/routers/recurring_tasks.py`, `api_service/services/recurring_tasks_service.py` |
| Routers | `api_service/api/routers/task_runs.py` (17), `executions.py` (107 — request/response field names, snapshot keys) |
| Legacy numbered modules | `moonmind/workflows/temporal/activities/task_5_14.py`, `workflows/task_5_14_workflow.py` |
| Worker/runtime | `moonmind/workflows/temporal/service.py` (33), `worker_runtime.py` (24), `workflows/run.py` (59), `story_output_tools.py` (39) — review each: much of this is reserved Task Queue plumbing |

### F. Frontend (hard-switch plan §10, Phase 5)

~1,800 task occurrences across 30 files in `frontend/src` (excluding generated). Clusters: `workflow-start.tsx` (280) and its test (607), `workflow-detail.tsx` (241) and test (303), `workflow-list.tsx`/test (132), `lib/temporalTaskEditing.ts` (128 + module name itself), `lib/query/keys.ts`, `entrypoints/proposals.tsx`, plus `taskId` in 11 files (122 occurrences). `frontend/src/generated/openapi.ts` regenerates after the backend schema rename. No `/tasks` UI routes remain (already cut over).

### G. Database (hard-switch plan §14, Phase 6)

Task-named tables in `api_service/db/models.py`: `task_step_templates`, `task_step_template_versions`, `task_step_template_favorites`, `task_step_template_recents`, `recurring_task_definitions`, `recurring_task_runs`, `task_proposals` (plus enums `TaskTemplateScopeType`, `TaskTemplateReleaseStatus`, `RecurringTask*`). `WorkflowTaskStatus` / `WorkflowTaskName` enums need case-by-case review (may be Temporal-native naming). Alembic references in `0b8e4befb8e5_initial_clean_migration.py`, `311_proposal_delivery_records.py`, `312_workflow_execution_source_mapping_cutover.py`.

### H. Enforcement gap (hard-switch plan §18, Phase 9)

No banned-term lint/CI check exists yet, which is how the stale `docs/Tasks` links survived two rename PRs. This should land early (docs-scope first), not last.

## Execution plan

Ordered work packages, each one PR-sized unless noted. WP1–WP4 are doc-only and safe immediately. WP5–WP8 are the code hard switch and must follow the hard-switch plan's Phase 3.1 in-flight compatibility gate (MM-730: `TEMPORAL_USER_WORKFLOW_CONTRACT_MODE`, distinct v2 Task Queue, cutover record, release notes path).

**WP1 — Fix broken links and agent misdirection (done).**
Repoint all 79 `docs/Tasks/...` references to current locations (`docs/Steps/...`, `docs/Workflows/...`); update `AGENTS.md`/`GEMINI.md` doc pointers and remove the outdated `specs/<feature>/` guidance in favor of `docs/tmp/`; fix the three test instruction strings. Verification: `rg -n "docs/Tasks" --hidden -g '!.git'` returns nothing outside gitignored `artifacts.*` dirs.

**WP2 — README + terminology decision (done).**
Decide "task-scoped session" → "workflow-scoped session" (or alternative), then sweep `README.md`, `docs/MoonMindArchitecture.md`, and ManagedAgents docs for it. Fix README "submit a task" / "first task" copy. Leave "recurring tasks" wording until WP7 renames the feature, or adopt "scheduled workflows" copy now and note the code lag.

**WP3 — Conceptual doc rewrites (done).**
Rewrite `docs/UI/MissionControlArchitecture.md` → `docs/UI/WorkflowConsoleArchitecture.md` on the Workflow Execution console posture (route tables reflect the already-shipped `/workflows/*` routes). Rename `RunHistoryAndRerunSemantics.md` → `WorkflowRunHistoryAndNewRunSemantics.md`. Delete `docs/Temporal/TaskExecutionCompatibilityModel.md` and update the doc test that pins it. Update all inbound links (repo-wide grep per the compatibility policy: no partial migrations).

**WP4 — Docs prose sweep + enforcement (docs/instruction scope) (done).**
Sweep the section-C list, rewriting product-concept prose while preserving still-live code identifiers as literals. Add the §18 banned-term CI check scoped to `docs/`, `README.md`, `AGENTS.md`, `GEMINI.md` now (unqualified `MoonMind task`, `task-oriented`, `task-first`, `taskId` outside code-literal contexts, `docs/Tasks` paths), so docs can't regress while code work proceeds.

**WP5 — Backend API schema and service rename (hard-switch Phases 3–4).**
Per hard-switch plan §8/§11: `TaskPayload` and `moonmind/workflows/tasks/` → workflow-execution contract naming; `task_proposals` / `task_runs` / `task_step_templates` routers and services → workflow proposal / workflow run / step-template naming; remove `taskId`-shaped response fields. Constitution Temporal-contract rules apply: workflow/activity/signal/update payload renames require the MM-730 versioned cutover (v2 Task Queue, drain/terminate record per environment) and workflow-boundary tests including one prior-payload-shape compatibility case. Review `task_5_14*` modules for deletion. Multi-PR.

**WP6 — Frontend rename (hard-switch Phase 5).**
Regenerate `openapi.ts` from WP5; rename `temporalTaskEditing.ts`, `taskId` props/state, query keys, test fixtures, and any visible copy. Tests in the same PRs.

**WP7 — Database rename (hard-switch Phase 6).**
Alembic migrations renaming `task_step_template*`, `recurring_task_*`, `task_proposals` tables, indexes, FKs, and enums; resolve `WorkflowTaskStatus`/`WorkflowTaskName` after review. Pre-release policy: rename outright, no compat views or aliases.

**WP8 — Tests and remaining identifiers (hard-switch Phase 8).**
Sweep `tests/` for residual task naming not already updated by WP5–WP7; rename test module paths (`test_task_proposals.py`, etc.).

**WP9 — Full enforcement + acceptance (hard-switch Phases 9–10).**
Extend the WP4 CI check to code scope (banned: `taskId`, `task_id` product fields, `/tasks` routes, `TaskService`, unqualified task terms in OpenAPI). Verify all 15 acceptance criteria in hard-switch plan §20. Ship the breaking-release note from §19 Phase 10.

## Out of scope

- `.specify/` templates (`tasks-template.md`, etc.): "tasks" there is MoonSpec/spec-kit implementation-task vocabulary, a different concept from the MoonMind product entity; also `specs/` is no longer checked-in guidance.
- Gitignored/local `artifacts.*` directories containing stale paths in cached RAG context.
- Reserved Temporal-native and qualified-external task terms (see top).
