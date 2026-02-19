# Task Architecture Implementation Plan

Status: Draft  
Owners: MoonMind Engineering  
Last Updated: 2026-02-16

## 1. Purpose

Define a phased implementation plan to deliver the new Task architecture across API, UI, and worker runtimes.

Primary outcomes:

- Canonical `type="task"` queue contract
- Typed Task UI submission flow
- Runtime-neutral worker execution with `prepare -> execute -> publish` wrapper stages
- Safe migration from legacy `codex_exec` / `codex_skill` jobs

## 2. Scope

In scope:

- Queue payload contract and validation
- Worker execution plan and runtime adapters
- UI submit/detail updates for typed Task jobs
- Capability-based claim eligibility and publish semantics
- Migration, observability, and security hardening

Out of scope:

- Replacing SpecKit workflow architecture
- Replacing Orchestrator run architecture
- Guaranteeing identical model outputs across runtimes

## 3. Phased Delivery

### Phase 0: Contract Freeze and Migration Guardrails

Backend/API:

- Add canonical `type="task"` payload models and validation.
- Add compatibility parser that normalizes `codex_exec` and `codex_skill` into canonical internal task representation.
- Add capability derivation utility:
  - runtime capability (`codex` | `gemini` | `claude`)
  - `git`
  - `gh` when `publish.mode = pr`
  - skill-specific capability extensions

UI:

- Keep current queue pages functional.
- Add/update frontend types for canonical Task payload contract.

Workers:

- Accept canonical task payload in claim/dispatch path.
- Keep legacy handlers active.

Exit criteria:

- Queue accepts `type="task"` and legacy job types.
- Canonical payload validation is enforced server-side.

### Phase 1: Worker Stage Plan Engine (`prepare -> execute -> publish`)

Backend/API:

- Implement internal task plan builder for canonical task jobs.
- Emit stage lifecycle events:
  - `moonmind.task.prepare`
  - `moonmind.task.execute`
  - `moonmind.task.publish`
- Add standard artifacts:
  - `task_context.json`
  - stage logs
  - patch artifact when applicable

Workers:

- Implement shared `prepare` and `publish` stages.
- Keep runtime-specific `execute` stage per adapter.
- Implement branch resolution at execution time:
  - resolve repo default branch
  - compute starting/new/effective branches
  - deterministic auto-branch naming

UI:

- Display stage events/log groups in queue task detail view.

Exit criteria:

- End-to-end task run produces stage events and required artifacts.
- Legacy jobs execute through the same internal stage engine.

### Phase 2: Runtime Adapters and Skill Materialization

Backend/API:

- Finalize runtime adapter contract (`codex`, `gemini`, `claude`, `universal`).
- Enforce runtime override precedence:
  1. `task.runtime.model`/`task.runtime.effort`
  2. worker defaults
  3. CLI defaults

Workers:

- Materialize per-run skills workspace and adapter symlinks:
  - `.agents/skills -> skills_active`
  - `.gemini/skills -> skills_active`
- Execute selected skill when `skill.id != auto`; direct instructions when `skill.id = auto`.
- Keep Git operations in publish stage only.

UI:

- Show runtime, model, effort, skill metadata consistently in task detail.

Exit criteria:

- Canonical task payload runs across runtime-specific and universal workers.
- Both skill and direct execution paths produce consistent events/artifacts.

### Phase 3: Typed Task UI Submit (Replace Generic Raw JSON Flow)

UI:

- Replace generic queue submit with typed Task form.
- Required field:
  - `instructions`
- Optional fields:
  - `skill`
  - `runtime`
  - `model`
  - `effort`
  - `repository`
  - `startingBranch`
  - `newBranch`
  - `publishMode`
- Submit only canonical queue jobs:
  - `type="task"`
  - canonical task payload
- Add validation and help text for execution-time defaults.

Backend/API:

- Keep `POST /api/queue/jobs` surface unchanged.
- Enforce canonical task validation and reject malformed typed payloads.

Workers:

- No new behavior required beyond canonical payload support.

Exit criteria:

- Users can submit tasks without raw JSON payload editing.
- Submitted payloads conform to canonical task schema.

### Phase 4: Fleet Rollout and Legacy Deprecation Window

Backend/API:

- Add migration telemetry:
  - job volume by type
  - failures by runtime/stage
  - publish outcome rates
- Add warnings for legacy job submissions.

Workers:

- Run mixed fleet with capability checks:
  - runtime-specific workers
  - optional universal workers
- Keep compatibility handlers behind feature flag.

UI:

- Remove legacy job type selection paths.
- Add filters for runtime, stage status, and publish mode.

Exit criteria:

- Majority of production queue volume is `type="task"`.
- No regression in completion or publish success during mixed-fleet rollout.

### Phase 5: Security Hardening and Cleanup

Backend/API:

- Add optional auth reference fields for hardened secret resolution:
  - `auth.repoAuthRef`
  - `auth.publishAuthRef`
- Strengthen redaction and policy enforcement.

Workers:

- Support Vault-backed secret resolution and env-token fallback.
- Enforce deny-by-default for missing capability/policy requirements.

UI:

- Keep token-free submission model.

Exit criteria:

- No secret material appears in queue payloads/events/artifacts.
- Legacy `codex_exec`/`codex_skill` submission paths can be disabled.

## 4. Cross-Phase Validation

Per phase validation must include:

- Unit tests: `./tools/test_unit.sh`
- Integration tests for queue claim/execution/publish flows
- Artifact checks:
  - stage logs present
  - `task_context.json` present
  - patch artifact present when repository changes
- Event checks:
  - stage start/finish
  - branch/default resolution events
  - publish result events
- Security checks:
  - no token-like values in logs/artifacts
  - policy checks enforced for repository and capability scope

## 5. Rollout Gates

Advance phases only when all gates are met:

1. Functional gate: canonical task jobs execute successfully end-to-end.
2. Compatibility gate: legacy jobs continue to run in migration window.
3. Observability gate: stage events/artifacts are complete and queryable.
4. Security gate: no secret leakage and policy checks are enforced.
5. UX gate: typed Task form is stable and default behaviors are clear.

## 6. Deliverables Summary

- Canonical task schema and validation
- Stage-plan execution engine (`prepare`, `execute`, `publish`)
- Runtime adapter and skill materialization support
- Typed Task UI submission and detail experience
- Migration telemetry and legacy compatibility controls
- Hardened secret resolution path and cleanup plan

## 7. Related Documents

- `docs/TaskArchitecture.md`
- `docs/TaskUiArchitecture.md`
- `docs/TailwindStyleSystem.md`
- `docs/CodexTaskQueue.md`
- `docs/UnifiedCliSingleQueueArchitecture.md`
- `docs/WorkerGitAuth.md`
- `docs/SecretStore.md`
